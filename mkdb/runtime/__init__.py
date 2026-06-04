from mkdb.objects import runtime_options as __runtime_options__
from mkdb.objects import base_object

class runtime_args(base_object):
    def __init__(self, data: dict = {}):
        self.config = "./config.json"
        super().__init__(data)

class runtime_options(__runtime_options__):
    def __init__(self, data:dict={}):
        self.args:runtime_args = runtime_args()
        super().__init__(data)


runtime_settings = runtime_options()
