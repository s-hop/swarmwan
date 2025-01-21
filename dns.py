import asyncio, socket

class DNSCatchall:
    def __init__(self, ip_address):
        self.ip_address = ip_address
        self.socket = None
        self.running = False

    async def handler(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setblocking(False)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            self.socket.bind(('0.0.0.0', 53))
            
            self.running = True
            print(f'DNS Catchall started on 0.0.0.0:53 redirecting to {self.ip_address}')

            while self.running:
                try:
                    # Non-blocking receive
                    request, client = self.socket.recvfrom(256)
                    
                    # Construct DNS response
                    response = request[:2]  # request id
                    response += b'\x81\x80'  # response flags
                    response += request[4:6] + request[4:6]  # qd/an count
                    response += b'\x00\x00\x00\x00'  # ns/ar count
                    response += request[12:]  # original request body
                    response += b'\xC0\x0C'  # pointer to domain name at byte 12
                    response += b'\x00\x01\x00\x01'  # type and class (A record / IN class)
                    response += b'\x00\x00\x00\x3C'  # time to live 60 seconds
                    response += b'\x00\x04'  # response length (4 bytes = 1 ipv4 address)
                    response += bytes(map(int, self.ip_address.split('.')))  # ip address parts
                    
                    # Send response
                    self.socket.sendto(response, client)
                
                except OSError as e:
                    # Handle non-blocking socket errors
                    if e.args[0] == 11:  # EAGAIN/EWOULDBLOCK
                        await asyncio.sleep_ms(50)
                    else:
                        print(f'Socket error: {e}')
                        break

                await asyncio.sleep_ms(10)
        
        except Exception as e:
            print(f'DNS Handler error: {e}')
            with open('crash.txt', 'a') as f:
                f.write(f'DNS Handler error: {e}')
        finally:
            if self.socket:
                self.socket.close()
    
    def stop(self):
        self.running = False