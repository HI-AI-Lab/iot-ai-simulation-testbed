import socket

PORT = 60001  # Change this if you used a different port in COOJA

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    print(f"Connecting to COOJA Serial Socket on port {PORT} ...")
    sock.connect(('localhost', PORT))
    print(f"Connected. Listening for messages from the mote...")

    while True:
        data = b''
        # Read until newline
        while not data.endswith(b'\n'):
            part = sock.recv(1024)
            if not part:
                print("Connection closed.")
                return
            data += part
        msg = data.decode().strip()
        print("From mote:", msg)

        if msg.startswith("ASK"):
            # In a real scenario, parse and compute the best parent here
            # For now, always reply "1\n"
            reply = "1\n"
            print(f"Replying: {reply.strip()}")
            sock.sendall(reply.encode())

if __name__ == '__main__':
    main()
