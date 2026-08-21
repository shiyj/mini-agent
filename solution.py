import datetime
import socket

def main():
    now = datetime.datetime.now()
    hostname = socket.gethostname()
    print("Current date:", now.strftime("%Y-%m-%d %H:%M:%S"))
    print("Hostname:", hostname)

if __name__ == "__main__":
    main()