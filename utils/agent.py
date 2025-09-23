# server.py
import socket

HOST, PORT = "127.0.0.1", 5000

s = socket.socket()
s.bind((HOST, PORT))
s.listen(1)
print(f"Python server listening on {HOST}:{PORT}")

conn, addr = s.accept()
print("Connected by", addr)

while True:
    data = conn.recv(1024)
    if not data:
        break
    msg = data.decode().strip()
    print("Got from JS:", msg)

    # echo back with PONG
    reply = f"PONG"
    conn.sendall((reply + "\n").encode())
