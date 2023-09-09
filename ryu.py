from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ipv4, ethernet, ether_types, arp
from ryu.lib import dpid as dpid_lib
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from ryu.topology import event, switches
from ryu.topology.api import get_switch, get_link, get_all_host, get_host
from ryu.app.simple_monitor_13 import *
from ryu.lib import hub

from ryu.app import simple_switch_13
from webob import Response
import time
import json
import sys

simple_switch_instance_name = 'simple_switch_api_app'
url = '/simpleswitch/mactable'

try:
    f1 = open('custom.txt', 'r')
    lines = f1.readlines()

except Exception as e:
    print('Error:', e)
    exit()

# Defining the SimpleSwitch13 RyuApp
class SimpleSwitch13(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}

    # Comctructor
    def __init__(self, *args, **kwargs):
        super(SimpleSwitch13, self).__init__(*args, **kwargs)
        # Initializing the necessary data structures
        self.mac_to_port = {}
        self.topology_api_app = self
        self.dm_links = {}
        self.hs_links = {}
        self.h = []
        self.links = {}
        self.switch_list = []
        self.switches = []
        self.links_list = []
        self.n_hosts = 0
        self.datapaths = {}
        self.flows = {}

        self.num_s = int(lines[0].split()[0])
        self.num_h = int(lines[0].split()[1])

        for i in range(1, self.num_s + 1):
            self.flows[i] = []
        
        self.pathinfo = [[{'cost': 0, 'dl': 0, 'bw': 0}] * self.num_s for _ in range(self.num_s)]
        self.adj = [[0]*self.num_s for _ in range(self.num_s)] # contains all costs

        # Initializing the links and adjacency matrix using the input file data
        for i in range(1,len(lines)):
            l = lines[i].split()
            if l[0][0] == 'S' and l[1][0] == 'S':
                a, b = int(l[0][1:]) - 1, int(l[1][1:]) - 1
                dl, bw = int(l[3]), int(l[2])
                self.pathinfo[a][b] = {'cost': dl/bw, 'dl': dl, 'bw': bw}
                self.pathinfo[b][a] = {'cost': dl/bw, 'dl': dl, 'bw': bw}

                self.adj[a][b] = dl/bw
                self.adj[b][a] = dl/bw

        # Printing the adjacency matrix
        print("Link costs...")
        for i in range(len(self.adj)):
            for j in range(len(self.adj[i])):
                if i!=j:
                    if self.adj[i][j]==0:
                        print("switch",i,"- switch",j,": ",-1)
                    else:
                        print("switch",i,"- switch",j,": ",self.adj[i][j])

        wsgi = kwargs['wsgi']
        wsgi.register(SimpleSwitchController,
                      {simple_switch_instance_name: self})


    @set_ev_cls(event.EventSwitchEnter)
    def get_topology_data(self, ev):
        # Retrieve the list of switches from the topology API
        self.switch_list = get_switch(self.topology_api_app, None)

        # Store the ID of each switch in a list
        self.switches = [switch.dp.id for switch in self.switch_list]

        # Retrieve the list of links from the topology API
        self.links_list = get_link(self.topology_api_app, None)

        # For each link, store the source switch ID, destination switch ID, and source port number in a dictionary
        for link in self.links_list:
            self.links[(link.src.dpid, link.dst.dpid)] = link.src.port_no
        print ("switches ", self.switches)
        print ("links ", self.links)

    # This function is a decorator which is called when an OFPSwitchFeatures event is generated and it handles the event
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Add the datapath to the datapaths dictionary
        self.datapaths[datapath.id] = datapath

        # Install table-miss flow entry
        # We specify NO BUFFER to max_len of the output action due to
        # OVS bug. At this moment, if we specify a lesser number, e.g.,
        # 128, OVS will send Packet-In with invalid buffer_id and
        # truncated packet data. In that case, we cannot output packets
        # correctly.  The bug has been fixed in OVS v2.1.0.
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                          ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, 0, match, actions)

    # This function is used to add the flow entry to the switch
    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                             actions)]
        if buffer_id:
            mod = parser.OFPFlowMod(datapath=datapath, buffer_id=buffer_id,
                                    priority=priority, match=match,
                                    instructions=inst)
        else:
            mod = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                    match=match, instructions=inst)
        datapath.send_msg(mod)

    # This function is used to send the packet out of the specified port
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        # If you hit this you might want to increase
        # the "miss_send_length" of your switch

        # Check if the packet received is truncated or not
        if ev.msg.msg_len < ev.msg.total_len:
            self.logger.debug("packet truncated: only %s of %s bytes",
                              ev.msg.msg_len, ev.msg.total_len)
        
        # Get the message and datapath object
        msg = ev.msg
        datapath = msg.datapath
        dpid = datapath.id
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        # Parse the received packet
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        # Ignore LLDP packets
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        # Extract source and destination MAC addresses
        dst = eth.dst
        src = eth.src
	
        # If the packet is an ARP packet, extract the source and destination IP and MAC addresses
        pkt_arp = pkt.get_protocol(arp.arp)
        if pkt_arp:
            # Destination and source ip address
            d_ip = pkt_arp.dst_ip
            s_ip = pkt_arp.src_ip

            # Destination and source mac address (HW address)
            d_mac = pkt_arp.dst_mac
            s_mac = pkt_arp.src_mac
            
            if (s_ip, s_mac) not in self.h:
                self.hs_links[s_ip] = [dpid, in_port]
                self.hs_links[s_mac] = [dpid, in_port]
                self.h.append((s_ip, s_mac))
                self.dm_links[dpid] = s_mac
                
                print("hosts: ", self.h)
                print("host connections: ", self.hs_links)
            
            
        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})

        # learn a mac address to avoid FLOOD next time.
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # install a flow to avoid packet_in next time
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
            # verify if we have a valid buffer_id, if yes avoid to send both
            # flow_mod & packet_out
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                # self.add_flow(datapath, 2, match, actions, msg.buffer_id)
                return
            # else:
                # self.add_flow(datapath, 2, match, actions)
        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id,
                                  in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)
    
    # Function to find the shortest path between two vertices using Dijkstra's algorithm
    # @GFG Article
    def dijkstra(self, start_vertex, end_vertex):
        adjacency_matrix = self.adj
        NO_PARENT = -1
        n_vertices = len(adjacency_matrix[0])

        # shortest_distances[i] will hold the
        # shortest distance from start_vertex to i
        shortest_distances = [sys.maxsize] * n_vertices

        # added[i] will true if vertex i is
        # included in shortest path tree
        # or shortest distance from start_vertex to
        # i is finalized
        added = [False] * n_vertices

        # Initialize all distances as
        # INFINITE and added[] as false
        for vertex_index in range(n_vertices):
            shortest_distances[vertex_index] = sys.maxsize
            added[vertex_index] = False
            
        # Distance of source vertex from
        # itself is always 0
        shortest_distances[start_vertex] = 0

        # Parent array to store shortest
        # path tree
        parents = [-1] * n_vertices

        # The starting vertex does not
        # have a parent
        parents[start_vertex] = NO_PARENT

        # Find shortest path for all
        # vertices
        for i in range(1, n_vertices):
            # Pick the minimum distance vertex
            # from the set of vertices not yet
            # processed. nearest_vertex is
            # always equal to start_vertex in
            # first iteration.
            nearest_vertex = -1
            shortest_distance = sys.maxsize
            for vertex_index in range(n_vertices):
                if not added[vertex_index] and shortest_distances[vertex_index] < shortest_distance:
                    nearest_vertex = vertex_index
                    shortest_distance = shortest_distances[vertex_index]

            # Mark the picked vertex as
            # processed
            added[nearest_vertex] = True

            # Update dist value of the
            # adjacent vertices of the
            # picked vertex.
            for vertex_index in range(n_vertices):
                edge_distance = adjacency_matrix[nearest_vertex][vertex_index]
                
                if edge_distance > 0 and shortest_distance + edge_distance < shortest_distances[vertex_index]:
                    parents[vertex_index] = nearest_vertex
                    shortest_distances[vertex_index] = shortest_distance + edge_distance

        path = []
        current_vertex = end_vertex
        while current_vertex != NO_PARENT:
            path.append(current_vertex+1)
            current_vertex = parents[current_vertex]

        path.reverse()
        return path
    
    # This function is used to calculate the shortest path between two hosts
    def calculate_shortest_path(self, src, dst, bw):
        path = self.dijkstra(src-1, dst-1)

        # If available bandwidth is less than required bandwidth, return -1
        for i in range(len(path)-1):
            if self.pathinfo[path[i]-1][path[i+1]-1]['bw'] < bw:
                return -1

        return path
    
    # Define a method to install paths for the given source and destination nodes
    def install_path(self, s, d, type, bw_usr):
        # Create a list of the source and destination nodes
        h = [s, d]
        # Iterate over the list of nodes
        for src in h:
            for dst in h:
                # If the source node is not equal to the destination node
                if src != dst:
                    # Calculate the shortest path between the source and destination nodes
                    path = self.calculate_shortest_path(self.hs_links[src][0], self.hs_links[dst][0], bw_usr)
                    
                    # If the path is -1, then there is no path between the source and destination nodes
                    if path == -1:
                        return -1
                    
                    # If the path is not -1, then install the path
                    for i in range(0, len(path)):
                        switch = path[i]
                        # If the current node is not the first or last node in the path
                        if i != len(path) - 1 and i != 0:
                            # Calculate the next hop and previous hop nodes in the path
                            next_hop = path[i+1]
                            prev_hop = path[i-1]
                            
                            # Subtract the bandwidth requested by the user from the available bandwidth on the current link
                            self.pathinfo[switch-1][next_hop-1]['bw'] -= bw_usr

                            # Calculate the cost of the current link based on its available bandwidth and delay
                            self.adj[switch-1][next_hop-1] = (self.pathinfo[switch-1][next_hop-1]['dl'])/(self.pathinfo[switch-1][next_hop-1]['bw'])

                            # Update the cost of the current link in the pathinfo dictionary
                            self.pathinfo[switch-1][next_hop-1]['cost'] = (self.pathinfo[switch-1][next_hop-1]['dl'])/(self.pathinfo[switch-1][next_hop-1]['bw'])

                            # Get the input and output ports for the current switch
                            in_port = self.links[(switch, prev_hop)]
                            out_port = self.links[(switch, next_hop)]

                            # Get the current link information from the pathinfo dictionary
                            self.pathinfo[switch-1][next_hop-1]

                        # If the current node is the last node in the path
                        elif i == len(path) - 1:
                            
                            prev_hop = path[i-1]
                            
                            in_port = self.links[(switch, prev_hop)]
                            out_port = self.hs_links[dst][1]

                        # If the current node is the first node in the path
                        else:
                            # Calculate the next hop node in the path
                            next_hop = path[i+1]

                            # Subtract the bandwidth requested by the user from the available bandwidth on the current link
                            self.pathinfo[switch-1][next_hop-1]['bw'] -= bw_usr

                            # Calculate the cost of the current link based on its available bandwidth and delay
                            self.adj[switch-1][next_hop-1] = (self.pathinfo[switch-1][next_hop-1]['dl'])/(self.pathinfo[switch-1][next_hop-1]['bw'])

                            # Update the cost of the current link in the pathinfo dictionary
                            self.pathinfo[switch-1][next_hop-1]['cost'] = (self.pathinfo[switch-1][next_hop-1]['dl'])/(self.pathinfo[switch-1][next_hop-1]['bw'])
                            
                            in_port = self.hs_links[src][1]
                            out_port = self.links[(switch, next_hop)]
                        
                        datapath = self.datapaths[switch]
                        parser = datapath.ofproto_parser
                        actions = [parser.OFPActionOutput(out_port)]

                        if type == 'ip':
                            match = parser.OFPMatch(in_port=in_port, eth_type=0x0800, ipv4_src=src, ipv4_dst=dst)
                        elif type == 'mac':
                            match = parser.OFPMatch(in_port=in_port, eth_type=0x0800, eth_src=src, eth_dst=dst)

                        
                        self.flows[switch].append((in_port, src, dst, out_port))
                        self.add_flow(datapath, 1, match, actions)

        return 1

# This is a controller class for a simple switch.
class SimpleSwitchController(ControllerBase):
    # Constructor for the class
    def __init__(self, req, link, data, **config):
        super(SimpleSwitchController, self).__init__(req, link, data, **config)
        self.simple_switch_app = data[simple_switch_instance_name]

    # A PUT request is used to update the MAC table.
    # This method receives the PUT request, updates the MAC table and installs a new path.
    # Then returns a response accordingly.
    @route('simpleswitch', url, methods=['PUT'])
    def put_mac_table(self, req, **kwargs):

        # Access the SimpleSwitch instance from the data object
        simple_switch = self.simple_switch_app
        
        # Try to retrieve the data from the request's body and decode it as a json object
        try:
            new_entry = req.json if req.body else {}
        except ValueError:
            raise Response(status=400)

        if 'dst' in new_entry:
            # Try to install the new path with the given parameters
            try:
            
                res = simple_switch.install_path(new_entry['src'], new_entry['dst'], new_entry['type'], int(new_entry['bw']))

                # Check the result of the path installation and return an appropriate response
                if res == -1:
                    return Response(body="Not enough bandwidth\n")
                else:
                    return Response(body="Done\n")
            except Exception as e:
                print(e)
                return Response(status=500)

        # If the source is in the new entry
        elif 'src' in new_entry:
            try:
                ans = []
                # Loop through all the hosts in the network
                for i in range(1,len(simple_switch.h)+1):
                    src = '10.0.0.' + str(new_entry['src'])
                    dst = '10.0.0.' + str(i)
                    # If the source and destination are different
                    if src != dst:
                        res = simple_switch.dijkstra(simple_switch.hs_links[src][0]-1, simple_switch.hs_links[dst][0]-1)
                        print("HSrc: ",src," Dst:",dst ," switches", res)

                # Check the result of the path installation and return an appropriate response
                if res == -1:
                    return Response(body="Not enough bandwidth\n")
                else:
                    return Response(body="Done\n")
            except Exception as e:
                print(e)
                return Response(status=500) 

        # If the switch dpid is in the new entry
        else:
            dp = int(new_entry['dpid'])
            print(simple_switch.flows[dp])
            return Response(body = "Done\n")
            