"""
ParityManager — Reed-Solomon parity generation and verification for log segments.

Parity files: {store_path}/{service}_{NNN}.parity
These files contain the Reed-Solomon parity symbols for the corresponding .log segment.

External dependency: reedsolo >= 1.7  (pip install reedsolo)
Imports are deferred to operation methods so the module loads cleanly even
if reedsolo is not installed; a clear ImportError with install instructions
is raised only when a parity operation is invoked.
"""

import logging
import os

logger = logging.getLogger(__name__)

_REEDSOLO_MISSING = (
    "reedsolo is required for parity operations. "
    "Install it with: pip install reedsolo"
)


class ParityManager:
    def __init__(self, store_path: str, service: str, nsym: int = 10):
        """
        Parameters
        ----------
        store_path : absolute path to the store directory
        service    : store name (file prefix)
        nsym       : Reed-Solomon parity symbol count (default 10)
        """
        self.store_path = store_path
        self.service    = service
        self.nsym       = nsym

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _log_path(self, seq_int: int) -> str:
        return os.path.join(self.store_path, f"{self.service}_{seq_int:03d}.log")

    def _parity_path(self, seq_int: int) -> str:
        return os.path.join(self.store_path, f"{self.service}_{seq_int:03d}.parity")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, seq_int: int) -> None:
        """
        Read the .log segment, encode via Reed-Solomon, write parity to .parity file.
        Overwrites any existing .parity file.

        Parity file format: concatenated ECC symbols for each chunk
        (nsym bytes per chunk of chunk_size = nsize-nsym data bytes).
        This avoids the single-chunk limitation of GF(2^8) (max 255 bytes)
        and keeps parity files small (~nsym/chunk_size ≈ 4% of log size).
        """
        try:
            import reedsolo
        except ImportError:
            raise ImportError(_REEDSOLO_MISSING)

        log_path = self._log_path(seq_int)
        if not os.path.exists(log_path):
            logger.warning("generate: log segment %03d not found, skipping", seq_int)
            return

        with open(log_path, "rb") as fh:
            data = fh.read()

        codec = reedsolo.RSCodec(self.nsym)
        chunk_size = codec.nsize - codec.nsym  # data bytes per chunk (default 245)
        ecc_parts = []
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i + chunk_size]
            enc_chunk = bytearray(codec.encode(chunk))
            ecc_parts.append(enc_chunk[len(chunk):])  # only the nsym ECC bytes
        parity_bytes = b"".join(ecc_parts)

        parity_path = self._parity_path(seq_int)
        tmp_path = parity_path + ".tmp"
        with open(tmp_path, "wb") as fh:
            fh.write(parity_bytes)
        os.replace(tmp_path, parity_path)
        logger.debug("generate: parity written for segment %03d (%d bytes)", seq_int, len(parity_bytes))

    def verify_and_repair(self, seq_int: int) -> bool:
        """
        Verify and optionally repair a log segment using its parity file.

        Returns
        -------
        True  — segment is clean or was repaired successfully
        False — segment is corrupt beyond repair
        """
        try:
            import reedsolo
        except ImportError:
            raise ImportError(_REEDSOLO_MISSING)

        log_path    = self._log_path(seq_int)
        parity_path = self._parity_path(seq_int)

        if not os.path.exists(log_path):
            logger.warning("verify_and_repair: log segment %03d not found", seq_int)
            return False
        if not os.path.exists(parity_path):
            logger.warning("verify_and_repair: parity file for segment %03d not found; generating now", seq_int)
            self.generate(seq_int)
            return True   # freshly generated == clean by definition

        with open(log_path, "rb") as fh:
            log_bytes = fh.read()
        with open(parity_path, "rb") as fh:
            parity_bytes = fh.read()

        codec = reedsolo.RSCodec(self.nsym)
        chunk_size = codec.nsize - codec.nsym  # data bytes per chunk (default 245)
        repaired = bytearray()
        ecc_offset = 0
        try:
            for i in range(0, len(log_bytes), chunk_size):
                chunk = log_bytes[i:i + chunk_size]
                ecc = parity_bytes[ecc_offset:ecc_offset + codec.nsym]
                dec_tuple = codec.decode(bytes(chunk) + bytes(ecc))
                repaired.extend(dec_tuple[0])
                ecc_offset += codec.nsym
        except reedsolo.ReedSolomonError as exc:
            logger.error(
                "verify_and_repair: segment %03d is corrupt beyond repair: %s",
                seq_int, exc,
            )
            return False

        decoded_bytes = bytes(repaired)
        if decoded_bytes != log_bytes:
            # Corruption detected but repairable
            logger.warning(
                "verify_and_repair: segment %03d had corruption — repaired and rewritten",
                seq_int,
            )
            from src.server.event_log import emit as _emit
            _emit("recovery", f"store:{self.service}",
                  f"Segment {self.service}_{seq_int:03d}.log had corruption — repaired via Reed-Solomon")
            tmp_path = log_path + ".tmp"
            with open(tmp_path, "wb") as fh:
                fh.write(decoded_bytes)
            os.replace(tmp_path, log_path)

        return True

    def boot_verify_all(self, log_manager) -> None:
        """
        Called from store.setup() after LogManager is initialised.
        Iterates all sealed segments and calls verify_and_repair on each.
        The active (highest-numbered) segment is excluded from verification
        because it is still being appended to — its parity is always regenerated
        fresh instead so subsequent rollover-based parity generation is correct.
        """
        from src.server.event_log import emit as _emit
        all_segs = log_manager.list_segments()
        active   = log_manager.active_segment
        for seq_int in all_segs:
            if seq_int == active:
                # Always regenerate parity for the active segment — it may have
                # grown since the last time parity was written.
                try:
                    self.generate(seq_int)
                    logger.debug("boot_verify_all: regenerated parity for active segment %03d", seq_int)
                except ImportError:
                    logger.info(
                        "boot_verify_all: reedsolo not installed — parity skipped. "
                        "Install with: pip install reedsolo"
                    )
                except Exception as exc:
                    logger.warning("boot_verify_all: could not generate parity for active segment %03d: %s", seq_int, exc)
                continue
            try:
                ok = self.verify_and_repair(seq_int)
                if not ok:
                    logger.error("boot_verify_all: segment %03d could not be repaired", seq_int)
                    _emit("error", f"store:{self.service}",
                          f"Segment {self.service}_{seq_int:03d}.log is corrupt beyond repair")
            except ImportError:
                logger.info(
                    "boot_verify_all: reedsolo not installed — parity verification skipped. "
                    "Install with: pip install reedsolo"
                )
                return   # skip all remaining segments
            except Exception as exc:
                logger.warning("boot_verify_all: error on segment %03d: %s", seq_int, exc)
                _emit("warning", f"store:{self.service}",
                      f"Parity check error on segment {self.service}_{seq_int:03d}.log: {exc}")
