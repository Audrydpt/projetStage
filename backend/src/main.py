import socket
import argparse

from database import GenericDAL
from event_grabber import EventGrabber
from api import ThreadedFastAPIServer, FastAPIServer
from acic.metadata import AcMetadataEventReceiverThread

def getNetworkIp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.connect(('<broadcast>', 12345))  # 12345 is random port. 0 fails on Mac.
    return s.getsockname()[0]

if __name__ == "__main__":
    # init database
    print("Init database")
    dal = GenericDAL()
    del dal

    ip = getNetworkIp()

    # parse command line arguments using argparse
    parser = argparse.ArgumentParser(description="Event grabber")
    parser.add_argument("--port", type=int, default=5020, help="Port of the server")

    args = parser.parse_args()

    # init grabber
    print("Init grabber")
    grabber = EventGrabber()
    grabber.add_grabber("127.0.0.1",     8081)   # localhost

    if True and ip == "192.168.20.145":
        for i in ["44", "150"]:
            server_ip = f"192.168.20.{i}"
            if server_ip != ip:
                serv = AcMetadataEventReceiverThread(acichost=server_ip, acichostport=8081)
                if serv.is_reachable(timeout=0.2) and serv.is_streaming(timeout=0.2):
                    grabber.add_grabber(server_ip, 8081)

    if False:
        for i in range(40, 240):
            server_ip = f"192.168.20.{i}"
            if server_ip != ip:
                serv = AcMetadataEventReceiverThread(acichost=server_ip, acichostport=8081)
                if serv.is_reachable(timeout=0.2) and serv.is_streaming(timeout=0.2):
                    grabber.add_grabber(server_ip, 8081)
    grabber.start()

    # init web server
    print("Init web server")
    #server = FastAPIServer(grabber)
    server = ThreadedFastAPIServer(grabber, workers=4)
    server.start(port=args.port)
    # wait for the server to stop

    # stop grabber
    print("Stop grabber")
    grabber.stop()
    grabber.join()
    print("Bye")
