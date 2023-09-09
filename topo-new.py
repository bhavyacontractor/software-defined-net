from mininet.topo import Topo
from mininet.net import Mininet
from mininet.cli import CLI
from mininet.node import RemoteController, OVSController
from mininet.term import makeTerm, makeTerms
from mininet.link import TCLink
import time

try:
    f1 = open('custom.txt', 'r')
    lines = f1.readlines()

except Exception as e:
    print('Error:', e)
    exit()


class CustomTopo (Topo):

    def build(self):

        sh = {'S1': S1, 'S2': S2, 'S3': S3, 'S4':S4, 'S5':S5, 'S6':S6, 'S7':S7, 'S8':S8, 'S9':S9, 'S10':S10, 'H1': H1, 'H2': H2, 'H3': H3, 'H4':H4}

        S1 = self.addSwitch('s1')
        S2 = self.addSwitch('s2')
        S3 = self.addSwitch('s3')
        S4 = self.addSwitch('s4')
        S5 = self.addSwitch('s5')
        S6 = self.addSwitch('s6')
        S7 = self.addSwitch('s7')
        S8 = self.addSwitch('s8')
        S9 = self.addSwitch('s9')
        S10 = self.addSwitch('s10')

        H1 = self.addHost('h1')
        H2 = self.addHost('h2')
        H3 = self.addHost('h3')
        H4 = self.addHost('h4')
        
        for i in range(1,len(lines)):
            l = lines[i].split()
            # print(l)
            self.addLink(sh[l[0]], sh[l[1]], bw=int(l[2]), delay=int(l[3]))

topo = CustomTopo()
net = Mininet(topo, controller=RemoteController, link = TCLink)
net.start()

net.pingAll()

CLI(net)

net.stop()

