#!/usr/bin/env python3
"""
UDP Packet Monitor - View incoming UDP packets on port 5555
Run this temporarily to debug ESP32 announcements
"""

import socket
import json
from datetime import datetime

UDP_PORT = 5556  # Use different port to avoid conflict, or stop windtunnel service first

def monitor_udp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', UDP_PORT))
    sock.settimeout(1.0)
    
    print(f"Listening for UDP packets on port {UDP_PORT}...")
    print("Press Ctrl+C to stop\n")
    
    try:
        while True:
            try:
                data, addr = sock.recvfrom(2048)
                timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                
                print(f"\n{'='*70}")
                print(f"[{timestamp}] Packet from {addr[0]}:{addr[1]}")
                print(f"Raw bytes ({len(data)} bytes): {data}")
                print(f"\nDecoded: {data.decode('utf-8', errors='replace')}")
                
                # Try to parse as JSON
                try:
                    packet = json.loads(data.decode('utf-8'))
                    print(f"\nParsed JSON:")
                    print(json.dumps(packet, indent=2))
                except:
                    print("\n(Not valid JSON)")
                
                print('='*70)
                
            except socket.timeout:
                continue
                
    except KeyboardInterrupt:
        print("\n\nStopped monitoring.")
    finally:
        sock.close()

if __name__ == '__main__':
    print("UDP Packet Monitor")
    print("="*70)
    monitor_udp()
