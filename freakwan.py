# Copyright (C) 2023-2024 Salvatore Sanfilippo <antirez@gmail.com>
# All Rights Reserved
#
# This code is released under the BSD 2 clause license.
# See the LICENSE file for more information

Version="0.41"

_SEND_MAX_DELAY = const(2000) # Random delay in milliseconds of asynchronous
                             # packet transmission. From 0 to the specified
                             # value. Choosen randomly.

# When a message will be transmitted multiple times (num_tx > 1), there
# following values, in milliseconds, will configure the minimum and maximum
# random delay between retransmissions. The max is not guaranteed: we could
# have many packets on the send queue, or the channel may be busy.
_TX_AGAIN_MIN_DELAY = const(3000)
_TX_AGAIN_MAX_DELAY = const(8000)

_HELLO_MSG = const('>> sending HELLO ')
_AUTO_MSG = const('>> sending AUTO ')

import machine, time, random, gc, sys, io
import asyncio, select
import sx1262
from collections import OrderedDict
from machine import Pin, SoftI2C, ADC, SPI
from message import *
from clictrl import CommandsController
from dutycycle import DutyCycle
from keychain import Keychain


# The application itself, including all the WAN routing logic.
class FreakWAN:
    def __init__(self, logger, config, nodes, set_config_update_cb):
        self.logger = logger
        self.logger_tag = "FW"

        # Save the configuration data and register the function 
        # to call when config is updated from the web server.
        self.config = config
        self.config_updated = False
        set_config_update_cb(self.handle_config_update)
        
        # The 'nodes' class contains the IDs and the time in ticks and signal
        # strength of the last message received for devices seen
        # (updated when receiving messages).
        self.nodes = nodes

        # Init TX led
        if self.config['tx_led']:
            self.tx_led = Pin(self.config['tx_led']['pin'], Pin.OUT)
        else:
            self.tx_led = None

        # Init LoRa chip
        self.lora = sx1262.SX1262(self.config['sx1262'],self.receive_lora_packet,self.lora_tx_done)
        self.lora_reset_and_configure()

        # Create our CLI commands controller.
        self.cmdctrl = CommandsController(self)

        # Queue of messages we should send ASAP. We append stuff here, so they
        # should be sent in reverse order, from index 0.
        self.send_queue = []
        self.send_queue_max = 50 # Don't accumulate too many messages

        # Track the RSSI history for the last few messages, to show on the display.
        self.rssi_history = []
        self.rssi_history_max = 8
        # Initialize the history with -100, so that the display will show a flat line
        for i in range(self.rssi_history_max):
            self.rssi_history.append(-100)

        # Our keychain is responsible of handling keys and
        # encrypting / decrypting packets.
        self.keychain = Keychain()
        self.device_name = self.keychain.device_key_name

        # Configure the duty cycle tracker, use a period of 60 minutes
        # with 12 5min slots. Adjust according to regulations.
        self.duty_cycle = DutyCycle(slots_num=12,slots_dur=60*5)

        # The 'processed' dictionary contains messages IDs of messages already
        # received/processed. We save the ID and the associated message
        # in case we are the originators (in order to collect acks). The
        # message has also a timestamp, this way we can evict old messages
        # from this list, to avoid a memory usage explosion.
        #
        # Note that we have two processed dict: a and b. Together, they
        # hold all the recently processed messages, however we need two
        # since we slowly analyze all the elements of dict a and put them
        # into dict b only if it is not expired (otherwise we would retain
        # all the messages seen, for a long time, running out of memory).
        #
        # Follow these rules:
        # 1. To see if a message was processed, check both dicts.
        # 2. When adding new messages, always add in 'a'.
        self.processed_a = {}
        self.processed_b = {}

        # Start receiving. This will just install the IRQ
        # handler, without blocking the program.
        self.lora.receive()

        # This is the buffer used in order to accumulate the
        # command the user is typing directly in the MicroPython
        # REPL via UBS serial.
        self.serial_buf = ""

        # If false, disable logging of debug info to serial.
        self.serial_log_enabled = True
        
        # Asyncio tasks
        self.auto_msg_task = None
        self.hello_msg_task = None
        self.test_cycle_task = None

    # Restart
    def reset(self):
        machine.reset()

    # this is horrible, would be better if class members reference config
    # directly so always have the latest values. TODO: fix when have time.
    def handle_config_update(self,new_config):
        self.config.update(new_config)
        self.config_updated = True
        self.logger.log_sys(self.logger_tag, 'INFO', 'Config Updated')

    def update_rssi_history(self,rssi):
        self.rssi_history.append(rssi)
        # Only keep the last 8 items
        if len(self.rssi_history) > self.rssi_history_max:
            self.rssi_history = self.rssi_history[-self.rssi_history_max:]
    
    def get_rssi_history(self):
        print(f'RSSI history = {self.rssi_history}')
        return self.rssi_history
    
    def get_lora_params(self):
        return self.config['lora']
    
    def get_duty_cycle(self):
        return self.duty_cycle.get_duty_cycle()

    async def cycle_configurations(self):
        cfg_dict = OrderedDict()
        
        with open(f'{self.config["FW"]["test_cycle_file"]}', 'r') as f:
            lines = f.read().split('\n')
            headers = lines[0].split(',')
            cfg_count = 0
            
            for l in lines[1:]:
                if l:  # Skip empty lines
                    values = l.split(',')
                    cfg_dict[cfg_count] = {}  # Create new dict for this row
                    
                    # Use zip() to pair headers with values
                    for h, v in zip(headers, values):
                        cfg_dict[cfg_count][h] = v
                    cfg_count += 1

        while True:
            cycle_duration = self.config['FW']['test_cycle_duration']
            
            now = self.logger.rtc.datetime
            start_of_day = now - (now % 86400)
            
            # Determine the index of the configuration based on the current time
            elapsed_seconds = int(now - start_of_day)
            cfg_index = (elapsed_seconds // cycle_duration) % len(cfg_dict)
            
            # Update LoRa configuration
            current_cfg = cfg_dict[cfg_index]
            lora_cfg = self.config['lora']
            for key in lora_cfg: # Apply only parameters that exist in the CSV
                if key in current_cfg:
                    lora_cfg[key] = int(current_cfg[key])
            
            # Apply new configuration
            self.lora_reset_and_configure()
            
            cfg_str = f'test={self.config["FW"]["test_cycle_file"]} cfg={cfg_index+1}/{len(cfg_dict)} lora={lora_cfg["fq"]}/{lora_cfg["bw"]}/{lora_cfg["sf"]}/{lora_cfg["cr"]}/{lora_cfg["pw"]}'
                
            print(cfg_str)
            self.logger.log_sys(self.logger_tag, 'INFO', cfg_str)
            
            # Wait until the start of the next cycle
            current_interval_start = start_of_day + (elapsed_seconds // cycle_duration) * cycle_duration
            time_remaining = current_interval_start + cycle_duration - now
            await asyncio.sleep(max(0, time_remaining))


    # Reset the chip and configure with the required paramenters.
    # Used during initialization and also in the TX watchdog if
    # the radio is stuck transmitting the current frame for some
    # reason.
    def lora_reset_and_configure(self):
        was_receiving = self.lora.receiving
        self.lora.begin()
        self.lora.configure(
            self.config['lora']['fq'],
            self.config['lora']['bw'],
            self.config['lora']['cr'],
            self.config['lora']['sf'],
            self.config['lora']['pw'])
        if was_receiving: self.lora.receive()
        print(f'LoRa Reset: {self.config["lora"]}')

    # Return the battery percentage using the equation of the
    # discharge curve of a typical lipo 3.7v battery.
#     def get_battery_perc(self):
#         volts = DeviceConfig.get_battery_microvolts()/1000000
#         if volts == 0: return 100
#         perc = 123-(123/((1+((volts/3.7)**80))**0.165))
#         return max(min(100,int(perc)),0)

    # Turn led on if state is True, off if it is False
    def set_tx_led(self,new_state):
        if not self.tx_led: return     # No led in this device
        if self.config['tx_led']['inverted']:
            new_state = not new_state
        if new_state:
            self.tx_led.on()
        else:
            self.tx_led.off()

    # Put a packet in the send queue. Will be delivered ASAP.
    # The delay is in milliseconds, and is selected randomly
    # between 0 and the specified amount.
    #
    # Check the send_messages_in_queue() method for the function
    # that actually transfers the messages to the LoRa radio.
    def send_asynchronously(self, m, max_delay=_SEND_MAX_DELAY, num_tx=1, relay=False):
        if len(self.send_queue) >= self.send_queue_max: return False
        m.send_time = time.ticks_add(time.ticks_ms(),urandom.randint(0,max_delay))
        m.num_tx = num_tx
        if relay: m.flags |= MSG_FLAG_PLEASE_RELAY
        self.send_queue.append(m)

        # Since we generated this message, if applicable by type we
        # add it to the list of messages we know about. This way we will
        # be able to resolve ACKs received, avoiding sending relays for
        # messages we originated and so forth.
        self.mark_as_processed(m)
        return True

    # Called when the packet was transmitted. Only useful to turn
    # the TX led off.
    def lora_tx_done(self):
        self.duty_cycle.end_tx()
        self.set_tx_led(False)

    # Send packets waiting in the send queue if duty cycle is below limit. 
    # TODO: Work out a better way to handle the duty cycle limit (currently can go over).
    def send_messages_in_queue(self):
#         if self.duty_cycle.get_duty_cycle() >= self.config['FW']['duty_cycle_limit']:
#             self.logger.log_sys(self.logger_tag, 'WARN', 'Duty cycle limit reached!')
#             return
        if self.lora.modem_is_receiving_packet(): return
        send_later = [] # List of messages we can't send, yet.
        while len(self.send_queue):
            m = self.send_queue.pop(0)
            if (time.ticks_diff(time.ticks_ms(),m.send_time) > 0):
                # If the radio is busy sending, waiting here is of
                # little help: it may take a while for the packet to
                # be transmitted. Try again in the next cycle. However
                # check if the radio looks stuck sending for
                # a very long time, and if so, reset the LoRa radio.
                if self.lora.tx_in_progress:
                    if self.duty_cycle.get_current_tx_time() > 60000:
                        warning = 'TX watchdog radio reset'
                        self.serial_log(warning)
                        self.logger.log_sys(self.logger_tag, 'WARN', warning)

                        self.lora_reset_and_configure()
                        self.lora.receive()
                    # Put back the message, in the same order as
                    # it was, before exiting the loop.
                    self.send_queue = [m] + self.send_queue
                    break

                # Send the message and turn the green led on. This will
                # be turned off later when the IRQ reports success.
                if m.send_canceled == False:
                    encoded = m.encode(keychain=self.keychain)
                    if encoded != None:
                        self.set_tx_led(True)
                        self.duty_cycle.start_tx()
                        self.lora.send(encoded)
                        time.sleep_ms(1)
                        self.logger.log_msg('tx', m.to_log_string())
                    else:
                        m.send_canceled = True

                # This message may be scheduled for multiple
                # retransmissions. In this case decrement the count
                # of transmissions and queue it back again.
                if m.num_tx > 1 and m.send_canceled == False and not self.config['FW']['quiet']:
                    m.num_tx -= 1
                    m.send_time = time.ticks_add(time.ticks_ms(),urandom.randint(_TX_AGAIN_MIN_DELAY,_TX_AGAIN_MAX_DELAY))
                    send_later.append(m)
            else:
                # Time to send this message yet not reached, send later.
                send_later.append(m)

        # In case of early break of the while loop, we have still
        # messages in the original send queue, so the new queue is
        # the sum of the ones to process again, plus the ones not
        # yet processed.
        self.send_queue = self.send_queue + send_later

    # Called upon reception of some message. It triggers sending an ACK
    # if certain conditions are met. This method does not check the
    # message type: it is assumed that the method is called only for
    # message type where this makes sense.
    def send_ack_if_needed(self,m):
        if not self.config['FW']['acks']: return  
        if m.type != MSG_T_DATA: return  
        if m.flags & MSG_FLAG_RELAYED: return 
        if m.nick == self.device_name: return  # Don't acknowledge our own messages.
        
        ack = Message(mtype=MSG_T_ACK,uid=m.uid,content=round(m.rssi),ttl=0)
        ack.nick = self.device_name
        self.send_asynchronously(
            ack,
            max_delay=self.config['FW']['ack_max_delay'])
        info = f'>> Sending ACK about {m.uid:04x}'
        self.serial_log(info)
        self.logger.log_sys(self.logger_tag, 'INFO', info)

    # Called for data messages we see for the first time. If the
    # originator asked for relay, we schedule a retransmission of
    # this packet, so that other peers can receive it.
    def relay_if_needed(self,m):
        if not self.config['FW']['relays']: return    
        if m.type != MSG_T_DATA: return          
        if not m.flags & MSG_FLAG_PLEASE_RELAY: return # No relay needed.
        # We also avoid relaying messages that are too strong: if the
        # originator of this message (or some other device that relayed it
        # already) is too near to us, it is unlikely that we will help
        # by transmitting it again. Actually we could just waste channel time.
        if m.rssi > self.config['FW']['relay_rssi_limit']: return
        if m.ttl <= 1: return # Packet reached relay limit.

        # Ok, we can relay it. Let's update the message.
        m.ttl -= 1
        m.flags |= MSG_FLAG_RELAYED  # This is a relay. No ACKs, please.
        self.send_asynchronously(
            m,
            num_tx=self.config['FW']['relay_num_tx'],
            max_delay=self.config['FW']['relay_max_delay'])
        info = f'>> Relaying {m.uid:04x} from {m.nick}'
        self.serial_log(info)
        self.logger.log_sys(self.logger_tag, 'INFO', info)

    # Return the message if it was already marked as processed, otherwise
    # None is returned.
    def get_processed_message(self,uid):
        m = self.processed_a.get(uid)
        if m: return m
        m = self.processed_b.get(uid)
        if m: return m
        return None

    # Mark a message received as processed. Not useful for all the kind
    # of messages. Only the ones that may be resent by the network
    # relays or retransmission mechanism, and we want to handle only
    # once. If the message was already processed, and thus is not added
    # again to the list of messages, True is returned, and the caller knows
    # it can discard the message. Otherwise we return False and add it
    # if needed.
    def mark_as_processed(self,m):
        if m.type == MSG_T_DATA:
            if self.get_processed_message(m.uid):
                return True
            else:
                self.processed_a[m.uid] = m
                return False
        else:
            return False

    # Remove old items from the processed cache
    def evict_processed_cache(self):
        count = 10 # Items to scan
        maxage = 60000 # Max cached message age in milliseconds
        while count and len(self.processed_a):
            count -= 1
            uid,m = self.processed_a.popitem()
            # Yet not expired? Move in the other dictionary, so we
            # know that the dictionary 'a' only has the items yet to
            # check for eviction.
            age = time.ticks_diff(time.ticks_ms(),m.ctime)
            if age <= maxage:
                self.processed_b[uid] = m
            else:
                self.serial_log(f'Cache evicted: {uid:04x}')

        # If we processed all the items of the 'a' dictionary, start again.
        if len(self.processed_a) == 0 and len(self.processed_b) != 0:
            self.processed_a = self.processed_b
            self.processed_b = {}

    # Called by the LoRa radio IRQ upon new packet reception.
    def receive_lora_packet(self, lora_instance, packet, rssi, snr, bad_crc):
        if self.config['FW']['check_crc'] and bad_crc: return
        m = Message.from_encoded(packet,self.keychain)
        if m:
            m.rssi = rssi
            m.snr = snr
            self.update_rssi_history(rssi)
            if bad_crc:
                m.flags |= MSG_FLAG_BADCRC
                self.logger.log(self.logger_tag, 'WARN', 'Message with bad CRC received: {m.type} {m.uid}')
            if m.no_key == True:
                # This message is encrypted and we don't have the
                # right key. Let's relay it, to help the network anyway.
                if self.mark_as_processed(m): return
                self.relay_if_needed(m)
                
            elif m.type == MSG_T_DATA:
                # Already processed? Return ASAP.
                if self.mark_as_processed(m):
                    info = f'<< Ignore duplicate msg: {m.uid:04x}'
                    self.serial_log(info)
                    self.logger.log_sys(self.logger_tag, 'INFO', info)
                    return

                # If this message is not relayed by some other node, then
                # it is a proof of recent node activity. We can update the
                # last seen time from what we have in memory for this node,
                # or we can add the node to active list, if it's new.
                if not m.flags & MSG_FLAG_RELAYED:
                    self.update_active_nodes(m.nick, rssi)

                # Report message to the user.
                msg_info = f'(rssi:{m.rssi}, snr:{m.snr}, ttl:{m.ttl}, flags:{m.flags>>3:04b})'
                channel_name = f'#{m.key_name} ' if m.key_name else ''
                user_msg = f'{m.nick}> {m.content}'
                if m.flags & MSG_FLAG_RELAYED: user_msg = f'{user_msg} [R]'
                if m.flags & MSG_FLAG_BADCRC:
                    user_msg = f'{user_msg} [BADCRC]'
                    self.logger.log(self.logger_tag, 'WARN', user_msg)
                self.serial_log(f'\033[32m{user_msg} {msg_info}\033[0m', force=True)

                # Log the message to the log file.
                self.logger.log_msg('rx', m.to_log_string())

                # Reply with ACK if needed.
                self.send_ack_if_needed(m)

                # Relay if needed.
                self.relay_if_needed(m)
                
            elif m.type == MSG_T_ACK:
                about = self.get_processed_message(m.uid)
                # Only log and process ACKs for messages that originated from us
                if about != None and about.nick == self.device_name:
                    log = f'<< Got ACK about {m.uid:04x} from {m.nick}'
                    self.serial_log(log)
                    self.logger.log_sys(self.logger_tag, 'INFO', log)
                    self.logger.log_msg('rx', m.to_log_string())
                    about.acks[m.nick] = True
                    self.update_active_nodes(m.nick, rssi)
                    # If we received ACKs from all the nodes we know about,
                    # stop retransmitting this message.
                    if self.nodes.count and len(about.acks) == self.nodes.count:
                        about.send_canceled = True
                        log = f'<< ACKs received from all {self.nodes.count} known nodes. Suppress resending.'
                        self.serial_log(log)
                        self.logger.log_sys(self.logger_tag, 'INFO', log)
                        
            elif m.type == MSG_T_HELLO:
                self.update_active_nodes(m.nick, rssi)

            else:
                err = f'<< Message type not implemented: {m.type}'
                self.serial_log(err)
                self.logger.log_sys(self.logger_tag, 'ERROR', err) 
        else:
            err = f'<< Can\'t decode message {repr(packet)}'
            self.serial_log(err)
            self.logger.log_sys(self.logger_tag, 'ERROR', err)
            
    # When a message is received, if node is known, update its info, else add
    # it to known list.         
    def update_active_nodes(self, nick, rssi):
        if self.nodes.seen(nick):
            self.nodes.update(nick, time.ticks_ms(), rssi)
        else:
            log = f'<< New node sensed: {nick}'
            self.serial_log(log)
            self.nodes.add(nick, time.ticks_ms(), rssi)        

    # Send HELLO messages from time to time. Evict nodes not refreshed
    # for some time from the nodes list.
    async def send_hello_message(self):
        hello_msg_period_min = self.config['FW']['hello_msg_period_min']
        hello_msg_period_max = self.config['FW']['hello_msg_period_max']
    
        # Send HELLO, if not in quiet mode.
        if not self.config['FW']['quiet']:
            self.serial_log(_HELLO_MSG)
            
            msg = Message(
                mtype=MSG_T_HELLO,  
                nick=self.device_name,
                seen=self.nodes.count
            )
            self.send_asynchronously(msg, max_delay=3000)

            # Wait until we need to send the next HELLO.
            await asyncio.sleep(urandom.randint(hello_msg_period_min,hello_msg_period_max))
    
    async def flush_nodes(self):
        flush_threshold = self.config['FW']['node_flush_threshold'] * 1000
        flush_interval = self.config['FW']['node_flush_interval']
        while True:
            # Evict nodes we haven't received a message from in a while.
            if self.nodes.count > 0:
                for node, data in self.nodes.active.items():
                    last_seen = data['last_seen_ms']
                    age = time.ticks_diff(time.ticks_ms(), last_seen)
                    if age <= flush_threshold:
                        continue
                    else:
                        self.nodes.timeout(node)
                        info = f'>> Flushing timedout node: {node}'
                        self.serial_log(info)
                        self.logger.log_sys(self.logger_tag, 'INFO', info)
            await asyncio.sleep(flush_interval)

    # This function is used in order to send automatic messages
    async def send_periodic_message(self):
        counter = 0
        while True:
            msg = Message(
                nick=self.device_name,
                content=f'{counter:04d}',
                ttl=self.config['FW']['ttl'],
                key_name=self.device_name)
            info = f'{_AUTO_MSG}{msg.uid:04x}'
            self.serial_log(info)
            self.logger.log_sys(self.logger_tag, 'INFO', info)
            self.send_asynchronously(msg,max_delay=3000,num_tx=1,relay=True)
            counter += 1
            await asyncio.sleep(urandom.randint(
                self.config['FW']['automsg_min_delay'],
                self.config['FW']['automsg_max_delay']))

    # This shows some information about the process in the debug console.
    def show_status_log(self):
        sent = self.lora.msg_sent
        msg = f'~{self.device_name} Sent:{sent} Q:{len(self.send_queue)} Free:{gc.mem_free()} DC:{self.duty_cycle.get_duty_cycle():.2f}'
        self.serial_log(msg)
        self.logger.log_sys(self.logger_tag, 'INFO', msg)

    # We want to reply to CLI inputs even if written directly in the
    # UART via USB, so that a user with the REPL open with the device
    # will be able to send commands directly.
    async def receive_from_serial(self):
        while True:
            await asyncio.sleep(0.1)
            while sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                try:
                    ch = sys.stdin.read(1)
                except:
                    continue
                if ch == '\n':
                    sys.stdout.write('\n')
                    cmd = self.serial_buf.strip()
                    self.serial_buf = ''
                    self.cmdctrl.exec_user_command(cmd,self.reply_to_serial)
                elif ord(ch) == 127:
                    # Backslash key.
                    self.serial_buf = self.serial_buf[:-1]
                    sys.stdout.write('\033[D \033[D') # Cursor back 1 position.
                else:
                    self.serial_buf += ch
                    sys.stdout.write(ch) # Echo

    # This method logs to the serial, but it is aware that we also let the
    # user write commands to the serial (see receive_from_serial() method).
    # So when we write to the serial, we hide the user input for a moment,
    # write the log, then restore the user input. Like an async readline
    # library would do.
    def serial_log(self,msg,force=False):
        if not self.serial_log_enabled and not force: return
        if len(self.serial_buf):
            sys.stdout.write('\033[2K\033[G') # Clean line, cursor on the left.
        sys.stdout.write(f'{msg}\r\n')
        if len(self.serial_buf):
            sys.stdout.write(self.serial_buf)

    # Callback to reply to CLI commands when they are received from
    # the USB serial.
    def reply_to_serial(self,msg):
        self.serial_log(msg,force=True)

    # This is the main control loop of the application, where we perform
    # periodic tasks, like sending messages in the queue. Other tasks
    # are handled by different tasks created at startup, at the end
    # of this file.
    async def cron(self):
        tick = 0
        
        if self.config['FW']['testing']:
            self.test_cycle_task = asyncio.create_task(self.cycle_configurations()) 
        if self.config['FW']['automsg']:
            self.auto_msg_task = asyncio.create_task(self.send_periodic_message())
        
#         self.hello_msg_task = asyncio.create_task(self.send_hello_message())
        self.flush_nodes_task = asyncio.create_task(self.flush_nodes())
        
        while True:
            if tick % 600 == 0: self.show_status_log()

            # If the configuration was updated (from web interface), we need to
            # reconfigure the LoRa radio.
            if self.config_updated:
                self.config_updated = False
                if not self.config['FW']['testing']:
                    # If the device was previously in testing mode and has the
                    # cycle config task running, cancel it and reset the radio
                    # with static settings.
                    if self.test_cycle_task:
                        self.test_cycle_task.cancel()
                        try:
                            await self.test_cycle_task
                        except asyncio.CancelledError:
                            pass        
                    self.lora_reset_and_configure()
                else:
                    # Reset task TODO: Only reset if test file has actually changed
                    if self.test_cycle_task:
                        self.test_cycle_task.cancel()
                        try:
                            await self.test_cycle_task
                        except asyncio.CancelledError:
                            self.test_cycle_task = asyncio.create_task(self.cycle_configurations())
                            pass        
                    else:
                        # Device not started with testing flag so start task now
                        self.test_cycle_task = asyncio.create_task(self.cycle_configurations())

#                 # Reset hello msg task TODO: Only reset if params actually changed
#                 if self.hello_msg_task:
#                     self.hello_msg_task.cancel()
#                     try:
#                         await self.hello_msg_task
#                     except asyncio.CancelledError:
#                         self.hello_msg_task = asyncio.create_task(self.send_hello_message())
#                         pass
                    
                if self.flush_nodes_task:
                    self.flush_nodes_task.cancel()
                    try:
                        await self.flush_nodes_task
                    except asyncio.CancelledError:
                        self.flush_nodes_task = asyncio.create_task(self.flush_nodes())
                        pass

            self.send_messages_in_queue()
            self.evict_processed_cache()

            # The tick time is randomized between 80 and 120
            # milliseconds instead of being exactly 100. This is
            # useful to always take the different nodes in desync:
            # a simple but effective way to avoid an all-together start
            # after listen-before-talk and other events.
            sleeptime = urandom.randint(800,1200)/10000
            await asyncio.sleep(sleeptime)
            tick += 1

    # Turn the exception into a proper stack trace.
    # Much better than str(exception).
    def get_stack_trace(self,exception):
        buf = io.StringIO()
        sys.print_exception(exception, buf)
        return buf.getvalue()

    def crash_handler(self,loop,context):
        # Try freeing some memory in order to avoid OOM during
        # the crash logging itself.
        self.send_queue = []
        self.processed_a = {}
        self.processed_b = {}
        gc.collect()

        # Capture the error as a string. It isn't of much use to have
        # it just in the serial, if nobody is connected via USB.
        stacktrace = self.get_stack_trace(context['exception'])
        print(stacktrace)

        # Print errors on the OLED, too. We want to immediately
        # recognized a crashed device.
        for stline in stacktrace.split('\n'):
            # TODO: print something to the display to signal the crash.
            pass

        # Let's log the stack trace on the filesystem, too.
        f = open('crash.txt','w')
        f.write(stacktrace)
        f.close()
