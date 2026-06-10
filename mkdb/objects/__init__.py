from copy import deepcopy
import datetime
import os
import sys
from typing import Any, Dict, List, Optional, Union


def formatItems(input:Union[dict, list]) -> Union[dict, list]:
    if type(input) == list:
        data = []
        for item in input:
            if isinstance(item, base_object):
                data.append(item.json)
            elif type(item) in [list, dict]:
                data.append(formatItems(item))
            elif type(item) in [tuple, int, float, str, bool]:
                data.append(item)
            elif callable(item):
                continue
    elif type(input) == dict:
        data = {}
        for key in input:
            if "__" in str(key):
                continue
            item = input[key]
            if isinstance(item, base_object):
                data[key] = item.json
            elif type(item) in [list, dict]:
                data[key] = formatItems(item)
            elif type(item) in [tuple, int, float, str, bool]:
                data[key] = item
            elif callable(item):
                continue
    return data
    
    

class base_object:
    def __init__(self, data:dict={}):
        self.update(data)

    def get(self, name, default=None):
        return self.__dict__.get(name, default)

    @property
    def json(self) -> dict:
        data = {}
        for name in self.__dict__:
            try:
                if "__" not in name:
                    item = self.__dict__[name]
                    if isinstance(item, base_object):
                        data[name] = item.json
                    elif type(item) in [list, dict]:
                        try:
                            data[name] = formatItems(item)
                        except Exception as e:
                            print("error", e)
                            print("Is dual ommited", name, type(item), item)
                            raise e
                    elif type(item) in [tuple, int, float, str, bool, None]:
                        data[name] = item
                    elif callable(item):
                        continue
                    else:
                        pass
                        #print("Is ommited", name, type(item), item)
                        #print(data)
            except Exception as e:
                print("base_object EXCEPTION!", name)
                print(e)
                raise e
        return data

    def update(self, data:dict):
        if type(data) not in [type(None), dict]:
            return
            #raise TypeError(f"base_object requires type 'Dict' on function update, Got: [{type(data)}: {data}]")
        if data is not None and type(data) == dict:
            for name in data:
                item = data[name]
                if name in self.__dict__:
                    if isinstance(self.__dict__[name], base_object):
                        self.__dict__[name].update(item)
                    elif type(item) in [list, int, float, str, dict, bool]:
                        self.__setattr__(name, item)
                

    def __repr__(self) -> str:
        return str(self.json) # "base_object: " + json.dumps(self.json)


def deep_update(mapping: dict, *updating_mappings: dict) -> dict:
    """Recursively update a dict. If a value is None, the key is removed."""
    updated_mapping = mapping.copy()
    for updating_mapping in updating_mappings:
        for k, v in updating_mapping.items():
            if v is None:
                updated_mapping.pop(k, None)
            elif k in updated_mapping and isinstance(updated_mapping[k], dict) and isinstance(v, dict):
                updated_mapping[k] = deep_update(updated_mapping[k], v)
            else:
                updated_mapping[k] = v
    return updated_mapping


def partition_object(data:Dict[str, Any], seperator="."):
    new = {}
    for key in data:
        if seperator in key:
            lv = key.split(seperator)
            i = 0
            gate = new
            for part in lv:
                if i < len(lv)-1:
                    if part not in gate:
                        gate[part] = {}
                    gate = gate[part]
                else:
                    gate[part] = data[key]
                i += 1
        else:
            new[key] = data[key]
    return new

def compact_object(data:dict, seperator=".", prev=None):
    new = {}
    for key in data:
        if type(data[key]) == dict:
            new.update(compact_object(data[key], seperator, (prev + "." if prev is not None else "") + key))
        else:
            new[(prev + "." if prev is not None else "") + key] = data[key]
    return new


class runtime_options(base_object):
    def __init__(self, data: dict = {}, parse=True):
        self.args:Union[base_object, dict]
        self.parse_error_exit:bool
        self.parse_types:bool
        self.parse_error_keep:bool
        self.partition_seperator:str
        self.__sys_argv__ = {}
        self.__arg_types__ = {}
        super().__init__(data)

        for pack in [
                ['args', {}],
                ['parse_error_exit', False],
                ['parse_types', True],
                ['parse_error_keep', False],
                ['partition_seperator', '.']
            ]:
            n, d = pack
            try:
                v = self.__getattribute__(n)
            except:
                v = None
            if v is None:
                self.__setattr__(n, d)
        
        if parse:
            self.parse()

    def __get_types__(self) -> dict[str, type]:
        types = self.args.json if isinstance(self.args, base_object) else self.args
        types = compact_object(types, self.partition_seperator)
        types = {x: type(types[x]) for x in types}
        return types
    
    def parse(self):
        import re
        data = {}
        types = self.__get_types__()
        self.__arg_types__ = types
        
        # Regex pattern to match key=value pairs
        # Handles: key=value, key="value", key='value', key="value with spaces"
        pattern = r'(\w+[\w\.]*)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s]*))'
        
        matches = re.findall(pattern, ' '.join(sys.argv[1:]))  # Skip script name (sys.argv[0])
        for match in matches:
            if match:
                key = match[0]
                # Get the value from whichever group matched (groups 1, 2, or 3)
                value = match[1] or match[2] or match[3] or ""
                data[key] = value
            
        self.__sys_argv__ = deepcopy(data)

        for key in list(data.keys()):
            value = data[key]
            try:
                data[key] = types.get(key)(value) # type: ignore
            except:
                if self.parse_error_exit:
                    raise ValueError(f"Could not set param[{key}] to Type of [{types.get(key)}], value: [{value}]")
                else:
                    if self.parse_error_keep == False:
                        print("Warning:", f"Could not set param[{key}] to Type of [{types.get(key)}], value: [{value}]")
                        del data[key]

        data = partition_object(data, self.partition_seperator)
        if type(self.args) == dict or isinstance(self.args, base_object):
            self.args.update(data)