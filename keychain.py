import os, hashlib
from collections import OrderedDict

# This class implements the packets encryption keychain. It loads and
# saves keys from/to disk, and implements encryption and decryption.
class Keychain:
    def __init__(self,keychain_dir="keys"):
        try: os.mkdir(keychain_dir)
        except: pass
        self.keychain_dir = keychain_dir
        self.keys = OrderedDict()
        self.device_key_name = ''
        self.load_keys()

    # Load keys in memory.
    def load_keys(self):
        key_names = os.listdir(self.keychain_dir)
        # The first key name in the list (*key) belongs to this device.
        device_key_name = key_names.pop(0)
        with open(f'{self.keychain_dir}/{device_key_name}', 'rb') as f:
            # Add to keys without the *
            self.device_key_name = device_key_name[1:]
            self.keys[self.device_key_name] = f.read()
        
        # add remaining keys to dictionary
        for key_name in key_names:
            try:
                with open(self.keychain_dir+"/"+key_name,'rb') as f:
                    key = f.read()    
                    self.keys[key_name] = key
            except: pass

    # This function expects an already encoded data packet, and
    # return its encrypted version.
    def encrypt(self, packet, key_name):
        key = self.keys[key_name]
        if key == None:
            raise Exception("No key with the specified name: "+str(key_name))

        key_len = len(key)
        encrypted = bytearray(packet)  # Convert bytes to mutable bytearray
        for i in range(len(packet)):
            encrypted[i] ^= key[i % key_len]  # XOR operation
            
        # Compute a key identifier (use first 3 bytes of SHA256 hash)
        key_id = hashlib.sha256(key).digest()[:3]

        return key_id + bytes(encrypted)  # Convert back to immutable bytes

    def decrypt(self, packet):
        key_id_received = packet[:3]
        encrypted_data = packet[3:]
        
        # Find the correct key by comparing hashed key identifiers
        matching_key_name = None
        for key_name, key_value in self.keys.items():
            key_digest = hashlib.sha256(key_value).digest()[:3]
            if key_digest == bytes(key_id_received):  # Convert bytearray to bytes for comparison
                matching_key_name = key_name
                break
                
        if matching_key_name:
            decrypted = self.encrypt(encrypted_data, matching_key_name)
            return matching_key_name, decrypted[3:]
        else:
            return None
