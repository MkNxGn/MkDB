import json

def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()
    
def write_file(path, content):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

def append_file(path, content):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(content)

def delete_file(path):
    import os
    os.remove(path)

def file_exists(path):
    import os
    return os.path.exists(path)

def list_files(directory):
    import os
    return os.listdir(directory)

def read_json(path):
    return json.loads(read_file(path))

def write_json(path, data):
    write_file(path, json.dumps(data, indent=4))