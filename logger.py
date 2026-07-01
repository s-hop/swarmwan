import asyncio
import os
import time
import vfs
from machine import I2C, SPI, Pin, RTC
from pcf8523 import PCF8523
from sdcard import SDCard


_MSG_LOG_T    = const('msg')
_SYS_LOG_T    = const('sys')
_MSG_LOG_DIR  = const('/sd/msg_log')
_SYS_LOG_DIR  = const('/sd/sys_log')
_MSG_LOG_HDRS = const('time,tx_rx,type,uid,nick,flags,rssi,snr,ttl,text\n')
_SYS_LOG_HDRS = const('time,tag,type,message\n')
_LOG_LIST_LEN = const(250)


class Logger:
    def __init__(self, rtc_pins, sd_pins):     
        self.i2c = I2C(rtc_pins['chan'], scl=Pin(rtc_pins['scl']), sda=Pin(rtc_pins['sda']))
        self.rtc = PCF8523(self.i2c)
        # Set a default time to prevent OverflowError (long int -> machine word)
        try:
            time.localtime(self.rtc.datetime)
        except OverflowError:
            self.rtc.datetime = time.localtime(1735689600)
        print('rtc initialised')

        self.spi = SPI(sd_pins['chan'], sck=Pin(sd_pins['sck']), mosi=Pin(sd_pins['mosi']), miso=Pin(sd_pins['miso']))
        self.sd = SDCard(self.spi, Pin(sd_pins['cs']))
        print('sd card initialised')

        # Mount the SD card as FS
        vfs.mount(self.sd, '/sd')
        print('sd mounted')
        
        # Create root log directories if not found
        if 'msg_log' not in os.listdir('/sd'):
            os.mkdir('/sd/msg_log')
        if 'sys_log' not in os.listdir('/sd'):
            os.mkdir('/sd/sys_log')
          
        # Set the current log file name based on current date, for easy
        # checking and creation of new files when the date changes.
        self.curr_date = self.get_date_str()
                
        # Ensure log directories for the current date
        self.msg_log_dir = f'{_MSG_LOG_DIR}/{self.curr_date}'
        self.sys_log_dir = f'{_SYS_LOG_DIR}/{self.curr_date}'
        self.create_new_log_dirs()

        # Determine the latest log file
        self.curr_msg_log_file = self.get_latest_log_file(self.msg_log_dir)
        self.curr_sys_log_file = self.get_latest_log_file(self.sys_log_dir)
        
        # Track number of logs written to file and create new files when limit reached.
        self.msg_log_count = self.update_log_count(self.curr_msg_log_file)
        self.sys_log_count = self.update_log_count(self.curr_sys_log_file)

        # Track index of current log file for current date. 
        self.msg_log_file_idx = self.get_log_file_idx(self.msg_log_dir)
        self.sys_log_file_idx = self.get_log_file_idx(self.sys_log_dir)
        
        # Logs are written to these buffers initially. Buffers are checked
        # periodically and logs are asynchronously written to file.
        self.msg_log_buffer = []
        self.sys_log_buffer = []

    # Called on init to ensure correct sum of logs in current file. 0 if new.
    def update_log_count(self, file_path):
        try:
            os.stat(file_path)  # Check if file exists
            with open(file_path, 'rb') as f:
                count = sum(1 for _ in f) - 1  # Subtract header row
                return max(count, 0)  # Ensure it doesn’t go negative
        except OSError:
            return 0  

    def get_log_file_idx(self, log_dir):
        return sum(1 for _ in os.listdir(log_dir))
    
    def set_log_file_idx(self, log_dir, idx):
        if log_dir == self.msg_log_dir:
            self.msg_log_file_idx = idx
        else:
            self.sys_log_file_idx = idx

    def create_new_log_dirs(self):
        if self.curr_date not in os.listdir(_MSG_LOG_DIR):
            os.mkdir(self.msg_log_dir)
            self.curr_msg_log_file = self.create_new_log_file(self.msg_log_dir)
        if self.curr_date not in os.listdir(_SYS_LOG_DIR):
            os.mkdir(self.sys_log_dir)
            self.curr_sys_log_file = self.create_new_log_file(self.sys_log_dir)

    def create_new_log_file(self, log_dir):
        """Creates a new log file with an incremented number."""
        files = sorted([f for f in os.listdir(log_dir)])
        new_idx = len(files) + 1
        self.set_log_file_idx(log_dir, new_idx)
        new_file = f"log_{new_idx:04d}.csv"
        full_path = f"{log_dir}/{new_file}"

        headers = _MSG_LOG_HDRS if "msg_log" in log_dir else _SYS_LOG_HDRS
        with open(full_path, "w") as f:
            f.write(headers)

        return full_path
                
    def get_latest_log_file(self, log_dir):
        """Finds the latest log file and checks if it's full before using it."""
        files = sorted([f for f in os.listdir(log_dir)])
 
        if not files:
            return self.create_new_log_file(log_dir)  # No logs exist yet
        
        latest_file = f"{log_dir}/{files[-1]}"  # Construct full path
        log_count = self.update_log_count(latest_file)
        
        if log_count < _LOG_LIST_LEN:
            return latest_file  # Continue using this file
        else:
            return self.create_new_log_file(log_dir)  # Create a new file if full
    
    # Retrieve the most recent + previous log file (if it exists) for web
    async def get_recent_logs(self, log_type, log_array, chunk_size=512):
        # Determine directory and file index based on log_type
        if log_type == 'msg':
            log_dir = self.msg_log_dir
            log_idx = self.msg_log_file_idx
        else:
            log_dir = self.sys_log_dir
            log_idx = self.sys_log_file_idx

        # Construct previous and current log file paths
        prev = f"{log_dir}/log_{log_idx - 1:04d}.csv" if log_idx > 1 else None
        curr = f"{log_dir}/log_{log_idx:04d}.csv"
        
        filepaths = (prev, curr)

        pos = 0
        for path in filepaths:
            if path is None:
                continue  # Skip if no previous log

            with open(path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:  # End of file
                        break
                    
                    if pos + len(chunk) > len(log_array):
                        self.log_sys('Logger', 'ERROR', 'Log file too large to read in full')
                        return pos 

                    log_array[pos:pos + len(chunk)] = chunk
                    pos += len(chunk)
                    await asyncio.sleep_ms(100)
            
        return pos  # Return final position in buffer

    def check_log_rotation(self, log_dir, log_type):
        """Checks if the current log file has exceeded 50 logs and rotates if necessary."""
        if log_type == _MSG_LOG_T and self.msg_log_count >= _LOG_LIST_LEN:
            self.msg_log_count = 0
            if log_dir == self.msg_log_dir:
                self.curr_msg_log_file = self.create_new_log_file(log_dir)
                return self.curr_msg_log_file
                
        if log_type == _SYS_LOG_T and self.sys_log_count >= _LOG_LIST_LEN:
            self.sys_log_count = 0
            if log_dir == self.sys_log_dir:
                self.curr_sys_log_file = self.create_new_log_file(log_dir)
                return self.curr_sys_log_file

        return self.curr_msg_log_file if log_type == _MSG_LOG_T else self.curr_sys_log_file

    # Monitors and changes current date if active past midnight, or RTC updated.
    # Saves calling get_date_str for every log.
    # TODO: replace with > check of seconds from epoch.
    async def check_date_task(self):
        while True:
            date = self.get_date_str()
            if date != self.curr_date:
                self.curr_date = date
                self.msg_log_dir = f'{_MSG_LOG_DIR}/{date}'
                self.sys_log_dir = f'{_SYS_LOG_DIR}/{date}'
                self.create_new_log_dirs()
            await asyncio.sleep(60)
            
    # Periodically checks the log buffers, transfers any logs to recent list and
    # writes them to file.
    async def check_buffer_task(self):
        while True:
            if self.msg_log_buffer:
                log = self.msg_log_buffer.pop(0)
                current_file = self.check_log_rotation(self.msg_log_dir, _MSG_LOG_T)
                with open(current_file, 'a') as f:
                    await self.write_in_chunks(f, log)
                self.msg_log_count += 1
                    
            if self.sys_log_buffer:
                log = self.sys_log_buffer.pop(0)
                current_file = self.check_log_rotation(self.sys_log_dir, _SYS_LOG_T)
                with open(current_file, 'a') as f:
                    await self.write_in_chunks(f, log)
                self.sys_log_count += 1

            await asyncio.sleep_ms(250)

    # Asynchronous file writing to prevent blocking
    async def write_in_chunks(self, file, log, chunk_size=32):
        for i in range(0, len(log), chunk_size):
            chunk = log[i:i + chunk_size]
            if not chunk:
                break
            file.write(chunk)
            file.flush()
            await asyncio.sleep_ms(10)

    def get_datetime_ISO_str(self):
        dt = time.localtime(self.rtc.datetime)
        return f'{dt[0]}-{dt[1]:02d}-{dt[2]:02d}T{dt[3]:02d}:{dt[4]:02d}:{dt[5]:02d}.000Z'

    def get_datetime_str(self):
        dt = time.localtime(self.rtc.datetime)
        return f'{dt[0]}-{dt[1]:02d}-{dt[2]:02d} {dt[3]:02d}:{dt[4]:02d}:{dt[5]:02d}'

    def get_time_str(self):
        dt = time.localtime(self.rtc.datetime)
        return f'{dt[3]:02d}:{dt[4]:02d}:{dt[5]:02d}'
    
    def get_time_s(self):
        return self.rtc.datetime

    def get_date_str(self):
        dt = time.localtime(self.rtc.datetime)
        return f'{dt[0]}-{dt[1]:02d}-{dt[2]:02d}'
    
    def set_rtc(self, secs):
        self.rtc.datetime = time.localtime(secs)

    def log_msg(self, txrx='', msg=''):
        self.msg_log_buffer.append(f'{self.get_time_str()},{txrx},{msg}\n')

    def log_sys(self, tag='', log_type='INFO', msg=''):
        self.sys_log_buffer.append(f'{self.get_time_str()},{tag},{log_type},{msg}\n')

    def add_to_recent(self, log_type, log):
        if log_type == _MSG_LOG_T:
            self.recent_msg_log.append(log)
            if len(self.recent_msg_log) > _LOG_LIST_LEN:
                # don't pop the headers at 0
                self.recent_msg_log.pop(1)
        elif log_type == _SYS_LOG_T:
            self.recent_sys_log.append(log)
            if len(self.recent_sys_log) > _LOG_LIST_LEN:
                # don't pop the headers at 0
                self.recent_sys_log.pop(1)
                
    async def get_log(self, log_type):
        output = ''
        
        if log_type == _MSG_LOG_T:
            for log in self.recent_msg_log:
                output += log
                await asyncio.sleep_ms(2)
            return output
        
        for log in self.recent_sys_log:
            output += log
            await asyncio.sleep_ms(2)
        return output
    
    # Check RTC & SD card init correctly. Prints and logs timestamp + message.
    def demo(self):
        if (not self.rtc or not self.sd):
            print('Must have a RTC and SD card to run this demo')
            return
        
        print(f'time.time =    {time.time()}')
        print(f'rtc.datetime = {self.rtc.datetime}')
        
        with open('/sd/demo.txt', 'w') as f:
            f.write('SD and RTC initiated!')
        with open('/sd/demo.txt', 'r') as f:
            msg = f.read()
            
        while (True):
            dt = self.get_datetime_str()
            log = f'<{dt}> {msg}'
            print(log)
            with open('/sd/demo_log.txt', mode='a') as file:
                file.write(log + '\n')
            time.sleep(1) 
        
        
if __name__ == '__main__':
    # Pins for PCF8523 RTC
    rtc_pins = {'chan': 1, 'scl': 7, 'sda': 6}
    # Pins for SD card
    sd_pins = {'chan': 1, 'sck': 10, 'mosi': 11, 'miso': 8, 'cs': 9}

    logger = Logger(rtc_pins, sd_pins)
    logger.demo()