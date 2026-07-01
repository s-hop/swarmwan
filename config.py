import sys
import os
import machine
from yaml_parser import parse_yaml, rebuild_yaml

class Config:
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.filename = self.get_filename()
        self.data = parse_yaml(self.get_contents())
        self.update_callback = None

    def get(self):
        return self.data
    
    def get_plain(self):
        plain_config = {}
        for group, items in self.data['decorated'].items():
            if group not in plain_config:
                plain_config[group] = {}
            for item in items: plain_config[group][item['id']] = item['value']
        
        plain_config.update(self.data['plain'])
        return plain_config
    
    def get_file_list(self):
        files = [f for f in os.listdir(self.config_dir) 
                 if not f.startswith('current')]
        files.append(self.filename)
        return ','.join(files)
    
    def get_filename(self):
        with open(f'{self.config_dir}/current.txt', 'r') as f:
            return f.read()    
        
    def get_contents(self):
        with open(f'{self.config_dir}/{self.filename}', 'r') as f:
            return f.read()
        
    def set_config_file(self, config):
        with open(f'{self.config_dir}/current.txt', 'w') as f:
            f.write(config)
        sys.exit()
        
    def web_update(self, updated_config):
        self.data.update({'decorated': updated_config})

        with open(f'{self.config_dir}/{self.filename}', 'w') as f:
            f.write(rebuild_yaml(self.data))

        self.data = parse_yaml(self.get_contents())

        if self.update_callback:  # Notify if callback is set
            try:
                self.update_callback(self.get_plain())
            except Exception as e:
                print("Callback error:", e)

    def set_update_callback(self, callback_function):
        self.update_callback = callback_function
