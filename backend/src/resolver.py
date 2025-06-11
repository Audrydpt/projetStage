from nmb.NetBIOS import NetBIOS
import socket
from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
import time

class Resolver:
    def __init__(self, timeout=1):
        self.timeout = timeout

    def resolve_dns(self, name):
        default_timeout = socket.getdefaulttimeout()
        
        try:
            socket.setdefaulttimeout(self.timeout)
            return socket.gethostbyname(name)
        except socket.timeout:
            return None
        except Exception as e:
            return None
        finally:
            socket.setdefaulttimeout(default_timeout)

    def resolve_netbios_broadcast(self, name):
        nb = NetBIOS(broadcast=True)
        try:
            ip_list = nb.queryName(name, timeout=self.timeout)
            return ip_list
        except Exception as e:
            return None
        finally:
            nb.close()

    def resolve_netbios_unicast(self, resolver, name):
        nb = NetBIOS(broadcast=False)
        try:
            ip_list = nb.queryName(name, ip=resolver, timeout=self.timeout)
            return ip_list
        except Exception as e:
            return None
        finally:
            nb.close()
    
    def resolve_mDNS(self, name):
        class MyListener(ServiceListener):
            def __init__(self, target_name):
                self.ip_addresses = []
                self.found = False
                self.target_name = target_name.lower()

            def add_service(self, zc, type_, name):
                service_name = name.split('.')[0].lower()
                info = zc.get_service_info(type_, name)
                if info and info.addresses:
                    ip = socket.inet_ntoa(info.addresses[0])
                if service_name == self.target_name:
                    if info and info.addresses:
                        self.ip_addresses.extend([socket.inet_ntoa(addr) for addr in info.addresses])
                        self.found = True

        zeroconf = Zeroconf()
        listener = MyListener(name)
        browser = ServiceBrowser(zeroconf, "_http._tcp.local.", listener)
        
        try:
            time.sleep(self.timeout)
            return listener.ip_addresses if listener.found else None
        finally:
            zeroconf.close()

    def resolve(self, name):
        ip = self.resolve_dns(name)
        if ip:
            return ip

        ips = self.resolve_netbios_broadcast(name)
        if ips:
            return ips[0]

        ips = self.resolve_mDNS(name)
        if ips:
            return ips[0]

        return None

if __name__ == '__main__':
    resolver = Resolver(timeout=1)
    name = "WIN-3972DR3ERU1"
    ip = resolver.resolve(name)
    print(f"IP trouvée pour {name}: {ip}")