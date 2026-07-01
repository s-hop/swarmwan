# Copyright (C) 2023 Salvatore Sanfilippo <antirez@gmail.com>
# All Rights Reserved
#
# This code is released under the BSD 2 clause license.
# See the LICENSE file for more information

import struct, time, urandom, machine, sys
from micropython import const

_MSG_TYPE_MASK = const(0x07)  # 0000 0111
_MSG_FLAGS_MASK = const(0xF8)  # 1111 1000
 
# Message types
MSG_T_DATA = const(1)
MSG_T_ACK = const(1<<1)
MSG_T_HELLO = const(1<<2)
# Message flags
MSG_FLAG_RELAYED = const(1<<3)     
MSG_FLAG_PLEASE_RELAY = const(1<<4)
MSG_FLAG_ENCR = const(1<<5)

# Virtual flags: not really in the packet header, but added
# in the message object representing the packet to provide
# further information.
MSG_FLAG_BADCRC = const(1<<7)

# The message object represents a FreakWAN message, and is also responsible
# of the decoding and encoding of the messages to be sent to the "wire".
class Message:
    def __init__(self, nick='---', content='---', uid=False, ttl=0, mtype=MSG_T_DATA, flags=0, rssi=0, seen=0, key_name=None):
        self.ctime = time.ticks_ms() # To evict old messages

        # send_time is only useful for sending, to introduce a random delay.
        self.send_time = self.ctime

        # Number of times to transmit this message. Each time the message
        # is transmitted, this value is reduced by one. When it reaches
        # zero, the message is removed from the send queue.
        self.num_tx = 1
        self.acks = {}  # Device IDs we received ACKs from
        self.type = mtype
        self.flags = flags
        self.nick = nick
        self.content = content
        self.uid = uid if uid != False else self.gen_uid()
        self.ttl = ttl              # Only DATA
        self.seen = seen            # Only HELLO
        self.rssi = rssi
        self.snr = 0
        self.key_name = key_name
        self.no_key = False         # True if it was not possible to decrypt.

        # If key_name is set, encoded messages will be encrypted, too.
        # When messages are decoded, key_name is set to the key that
        # decrypted the message, if any.

        # Sometimes we want to supporess sending of packets that may
        # already be inside the TX queue. Instead of scanning the queue
        # to look for the message, we just set this flag to True.
        self.send_canceled = False

    def to_log_string(self):
        if self.type == MSG_T_DATA:
            type_str = 'data'
        elif self.type == MSG_T_ACK:
            type_str = 'ack'
        elif self.type == MSG_T_HELLO:
            type_str = 'hello'
        return f'{type_str},{self.uid:04x},{self.nick},{self.flags>>3:04b},{self.rssi:.0f},{self.snr},{self.ttl},{self.content}'

    # Generate a 16 bit unique message ID.
    def gen_uid(self):
        return urandom.getrandbits(16)

    # Turn the message into its binary representation.
    def encode(self, keychain=None):
        # combine type and flags into a single byte mask
        combined = (self.type & _MSG_TYPE_MASK | self.flags & _MSG_FLAGS_MASK)
        
        if self.no_key == True:
            # Message that we were not able to decrypt. In this case
            # we saved the packet, and we just need to encode the
            # plaintext header and concatenate the saved packet from the
            # IV field till the end.
            return struct.pack("<BHB",combined,self.uid,self.ttl)+self.packet[4:]
        
        elif self.type == MSG_T_DATA:
            # Encode with the encryption flag set
            combined |= MSG_FLAG_ENCR
                
            # TODO: Determine a good fixed payload length. Currently using 20
            # bytes, which probably isn't long enough for useful messages.
            # DATA messages use:
            # 1 byte for  type+flags
            # 2 bytes for message UID
            # 1 byte for  TTL
            # -- nickname is probably not needed in final design as messages
            # -- are determined by enc key.
            # 3 bytes for nickname
            # 10 bytes for message content (0000-9999 + 8 byte padding)
            # 3 bytes for key_id
                
            header = struct.pack('<BHB', combined, self.uid, self.ttl)
            payload = keychain.encrypt(
                struct.pack('<13s', self.content.encode()),
                keychain.device_key_name)

            return header + payload
        
        elif self.type == MSG_T_ACK:
            # ACK content is a 2 byte RSSI for the DATA msg being ACKed 
            return struct.pack("<BHh3s",combined,self.uid,self.content,self.nick)
#         elif self.type == MSG_T_HELLO:
#             return struct.pack("<BB3s",combined,self.seen,self.nick)
        else:
            print("WARNING Message.encode() unknown msg type",self.type)
            return None

    # Fill the message with the data found in the binary representation
    # provided in 'msg'.
    def decode(self, msg, keychain=None):
        try:
            combined = struct.unpack("<B", msg[0:1])[0]
            
            # Extract type and flags
            mtype = combined & _MSG_TYPE_MASK
            flags = combined & _MSG_FLAGS_MASK

            # If the message is encrypted, try to decrypt it.
            if mtype == MSG_T_DATA and combined & MSG_FLAG_ENCR:
                plain = keychain.decrypt(msg[4:])

                # Messages for which we don't have a valid key
                # are returned in a "raw" form, useful only for relaying.
                # We signal that the message is in this state by
                # setting .no_key to True. We also decode what is in the
                # unencrypted part of the header.
                if not plain:
                    self.type = mtype
                    self.flags = flags
                    self.uid, self.ttl = struct.unpack("<HB", msg[1:4])
                    self.no_key = True
                    self.packet = msg
                    return True

                # If we have the key, the message is now decrypted.
                # We can continue with the normal code path after
                # populating key_name.
                self.key_name = plain[0]
                msg = msg[:4] + plain[1]
                
            # Decode according to message type.
            if mtype == MSG_T_DATA:
                self.type = mtype
                self.flags = flags
                self.uid, self.ttl = struct.unpack("<HB", msg[1:4])
                self.nick = self.key_name
                self.content = msg[4:].decode()
                return True
            elif mtype == MSG_T_ACK:
                self.type = mtype
                self.flags = flags
                self.uid,self.content = struct.unpack("<Hh",msg[1:5])
                self.nick = msg[5:8].decode()
                return True
#             elif mtype == MSG_T_HELLO:
#                 self.type = mtype
#                 self.flags = flags
#                 self.seen = struct.unpack("<B",msg[1:2])
#                 self.nick = msg[2:5].decode('utf-8')
#                 return True
            else:
                print(f'!!! Decoding message: wrong message type {mtype}')
                return False
        except Exception as e:
            print(str(e))
            return False

    # Create a message object from the binary representation of a message.
    def from_encoded(encoded, keychain):
        m = Message()
        if m.decode(encoded, keychain):
            return m
        else:
            return False
