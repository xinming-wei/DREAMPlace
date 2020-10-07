##
# @file   PlaceDB.py
# @author Yibo Lin
# @date   Apr 2018
# @brief  placement database 
#

import sys
import os
import re
import math
import time 
import numpy as np 
import logging
import Params
import dreamplace 
import dreamplace.ops.place_io.place_io as place_io 
import pdb 

datatypes = {
        'float32' : np.float32, 
        'float64' : np.float64
        }

class PlaceDB (object):
    """
    @brief placement database 
    """
    def __init__(self):
        """
        initialization
        To avoid the usage of list, I flatten everything.  
        """
        self.rawdb = None # raw placement database, a C++ object 

        self.num_physical_nodes = 0 # number of real nodes, including movable nodes, terminals, and terminal_NIs
        self.num_terminals = 0 # number of terminals, essentially fixed macros 
        self.num_terminal_NIs = 0 # number of terminal_NIs that can be overlapped, essentially IO pins
        self.node_name2id_map = {} # node name to id map, cell name 
        self.node_names = None # 1D array, cell name 
        self.node_x = None # 1D array, cell position x 
        self.node_y = None # 1D array, cell position y 
        self.node_orient = None # 1D array, cell orientation 
        self.node_size_x = None # 1D array, cell width  
        self.node_size_y = None # 1D array, cell height

        self.node2orig_node_map = None # some fixed cells may have non-rectangular shapes; we flatten them and create new nodes 
                                        # this map maps the current multiple node ids into the original one 

        self.pin_direct = None # 1D array, pin direction IO 
        self.pin_offset_x = None # 1D array, pin offset x to its node 
        self.pin_offset_y = None # 1D array, pin offset y to its node 

        self.net_name2id_map = {} # net name to id map
        self.net_names = None # net name 
        self.net_weights = None # weights for each net

        self.net2pin_map = None # array of 1D array, each row stores pin id
        self.flat_net2pin_map = None # flatten version of net2pin_map 
        self.flat_net2pin_start_map = None # starting index of each net in flat_net2pin_map

        self.node2pin_map = None # array of 1D array, contains pin id of each node 
        self.flat_node2pin_map = None # flatten version of node2pin_map
        self.flat_node2pin_start_map = None # starting index of each node in flat_node2pin_map

        self.pin2node_map = None # 1D array, contain parent node id of each pin 
        self.pin2net_map = None # 1D array, contain parent net id of each pin 

        self.rows = None # NumRows x 4 array, stores xl, yl, xh, yh of each row 

        self.regions = None # array of 1D array, placement regions like FENCE and GUIDE 
        self.flat_region_boxes = None # flat version of regions 
        self.flat_region_boxes_start = None # start indices of regions, length of num regions + 1
        self.node2fence_region_map = None # map cell to a region, maximum integer if no fence region  

        self.xl = None 
        self.yl = None 
        self.xh = None 
        self.yh = None 

        self.row_height = None
        self.site_width = None

        self.bin_size_x = None 
        self.bin_size_y = None
        self.num_bins_x = None
        self.num_bins_y = None
        self.bin_center_x = None 
        self.bin_center_y = None

        self.num_movable_pins = None 

        self.total_movable_node_area = None # total movable cell area 
        self.total_fixed_node_area = None # total fixed cell area 
        self.total_space_area = None # total placeable space area excluding fixed cells 

        # enable filler cells 
        # the Idea from e-place and RePlace 
        self.total_filler_node_area = None 
        self.num_filler_nodes = None

        self.routing_grid_xl = None 
        self.routing_grid_yl = None 
        self.routing_grid_xh = None 
        self.routing_grid_yh = None 
        self.num_routing_grids_x = None
        self.num_routing_grids_y = None
        self.num_routing_layers = None
        self.unit_horizontal_capacity = None # per unit distance, projected to one layer 
        self.unit_vertical_capacity = None # per unit distance, projected to one layer 
        self.unit_horizontal_capacities = None # per unit distance, layer by layer 
        self.unit_vertical_capacities = None # per unit distance, layer by layer 
        self.initial_horizontal_demand_map = None # routing demand map from fixed cells, indexed by (grid x, grid y), projected to one layer  
        self.initial_vertical_demand_map = None # routing demand map from fixed cells, indexed by (grid x, grid y), projected to one layer  

        self.dtype = None 

    def scale_pl(self, scale_factor):
        """
        @brief scale placement solution only
        @param scale_factor scale factor 
        """
        self.node_x *= scale_factor
        self.node_y *= scale_factor 

    def scale(self, scale_factor):
        """
        @brief scale distances
        @param scale_factor scale factor 
        """
        logging.info("scale coordinate system by %g" % (scale_factor))
        self.scale_pl(scale_factor)
        self.node_size_x *= scale_factor
        self.node_size_y *= scale_factor
        self.pin_offset_x *= scale_factor
        self.pin_offset_y *= scale_factor
        self.xl *= scale_factor 
        self.yl *= scale_factor
        self.xh *= scale_factor
        self.yh *= scale_factor
        self.row_height *= scale_factor
        self.site_width *= scale_factor
        self.rows *= scale_factor 
        self.total_space_area *= scale_factor * scale_factor # this is area 
        self.flat_region_boxes *= scale_factor
        # may have performance issue 
        # I assume there are not many boxes 
        for i in range(len(self.regions)): 
            self.regions[i] *= scale_factor 

    def sort(self):
        """
        @brief Sort net by degree. 
        Sort pin array such that pins belonging to the same net is abutting each other
        """
        logging.info("sort nets by degree and pins by net")

        # sort nets by degree 
        net_degrees = np.array([len(pins) for pins in self.net2pin_map])
        net_order = net_degrees.argsort() # indexed by new net_id, content is old net_id
        self.net_names = self.net_names[net_order]
        self.net2pin_map = self.net2pin_map[net_order]
        for net_id, net_name in enumerate(self.net_names):
            self.net_name2id_map[net_name] = net_id
        for new_net_id in range(len(net_order)):
            for pin_id in self.net2pin_map[new_net_id]:
                self.pin2net_map[pin_id] = new_net_id
        ## check 
        #for net_id in range(len(self.net2pin_map)):
        #    for j in range(len(self.net2pin_map[net_id])):
        #        assert self.pin2net_map[self.net2pin_map[net_id][j]] == net_id

        # sort pins such that pins belonging to the same net is abutting each other
        pin_order = self.pin2net_map.argsort() # indexed new pin_id, content is old pin_id 
        self.pin2net_map = self.pin2net_map[pin_order]
        self.pin2node_map = self.pin2node_map[pin_order]
        self.pin_direct = self.pin_direct[pin_order]
        self.pin_offset_x = self.pin_offset_x[pin_order]
        self.pin_offset_y = self.pin_offset_y[pin_order]
        old2new_pin_id_map = np.zeros(len(pin_order), dtype=np.int32)
        for new_pin_id in range(len(pin_order)):
            old2new_pin_id_map[pin_order[new_pin_id]] = new_pin_id
        for i in range(len(self.net2pin_map)):
            for j in range(len(self.net2pin_map[i])):
                self.net2pin_map[i][j] = old2new_pin_id_map[self.net2pin_map[i][j]]
        for i in range(len(self.node2pin_map)):
            for j in range(len(self.node2pin_map[i])):
                self.node2pin_map[i][j] = old2new_pin_id_map[self.node2pin_map[i][j]]
        ## check 
        #for net_id in range(len(self.net2pin_map)):
        #    for j in range(len(self.net2pin_map[net_id])):
        #        assert self.pin2net_map[self.net2pin_map[net_id][j]] == net_id
        #for node_id in range(len(self.node2pin_map)):
        #    for j in range(len(self.node2pin_map[node_id])):
        #        assert self.pin2node_map[self.node2pin_map[node_id][j]] == node_id

    @property
    def num_movable_nodes(self):
        """
        @return number of movable nodes 
        """
        return self.num_physical_nodes - self.num_terminals - self.num_terminal_NIs

    @property 
    def num_nodes(self):
        """
        @return number of movable nodes, terminals, terminal_NIs, and fillers
        """
        return self.num_physical_nodes + self.num_filler_nodes

    @property
    def num_nets(self):
        """
        @return number of nets
        """
        return len(self.net2pin_map)

    @property
    def num_pins(self):
        """
        @return number of pins
        """
        return len(self.pin2net_map)

    @property
    def width(self):
        """
        @return width of layout 
        """
        return self.xh-self.xl

    @property
    def height(self):
        """
        @return height of layout 
        """
        return self.yh-self.yl

    @property
    def area(self):
        """
        @return area of layout 
        """
        return self.width*self.height

    def bin_index_x(self, x): 
        """
        @param x horizontal location 
        @return bin index in x direction 
        """
        if x < self.xl:
            return 0 
        elif x > self.xh:
            return int(np.floor((self.xh-self.xl)/self.bin_size_x))
        else:
            return int(np.floor((x-self.xl)/self.bin_size_x))

    def bin_index_y(self, y): 
        """
        @param y vertical location 
        @return bin index in y direction 
        """
        if y < self.yl:
            return 0 
        elif y > self.yh:
            return int(np.floor((self.yh-self.yl)/self.bin_size_y))
        else:
            return int(np.floor((y-self.yl)/self.bin_size_y))

    def bin_xl(self, id_x):
        """
        @param id_x horizontal index 
        @return bin xl
        """
        return self.xl+id_x*self.bin_size_x

    def bin_xh(self, id_x):
        """
        @param id_x horizontal index 
        @return bin xh
        """
        return min(self.bin_xl(id_x)+self.bin_size_x, self.xh)

    def bin_yl(self, id_y):
        """
        @param id_y vertical index 
        @return bin yl
        """
        return self.yl+id_y*self.bin_size_y

    def bin_yh(self, id_y):
        """
        @param id_y vertical index 
        @return bin yh
        """
        return min(self.bin_yl(id_y)+self.bin_size_y, self.yh)

    def num_bins(self, l, h, bin_size):
        """
        @brief compute number of bins 
        @param l lower bound 
        @param h upper bound 
        @param bin_size bin size 
        @return number of bins 
        """
        return int(np.ceil((h-l)/bin_size))

    def bin_centers(self, l, h, bin_size):
        """
        @brief compute bin centers 
        @param l lower bound 
        @param h upper bound 
        @param bin_size bin size 
        @return array of bin centers 
        """
        num_bins = self.num_bins(l, h, bin_size)
        centers = np.zeros(num_bins, dtype=self.dtype)
        for id_x in range(num_bins): 
            bin_l = l+id_x*bin_size
            bin_h = min(bin_l+bin_size, h)
            centers[id_x] = (bin_l+bin_h)/2
        return centers 

    @property
    def routing_grid_size_x(self):
        return (self.routing_grid_xh - self.routing_grid_xl) / self.num_routing_grids_x 

    @property 
    def routing_grid_size_y(self):
        return (self.routing_grid_yh - self.routing_grid_yl) / self.num_routing_grids_y 

    def net_hpwl(self, x, y, net_id): 
        """
        @brief compute HPWL of a net 
        @param x horizontal cell locations 
        @param y vertical cell locations
        @return hpwl of a net 
        """
        pins = self.net2pin_map[net_id]
        nodes = self.pin2node_map[pins]
        hpwl_x = np.amax(x[nodes]+self.pin_offset_x[pins]) - np.amin(x[nodes]+self.pin_offset_x[pins])
        hpwl_y = np.amax(y[nodes]+self.pin_offset_y[pins]) - np.amin(y[nodes]+self.pin_offset_y[pins])

        return (hpwl_x+hpwl_y)*self.net_weights[net_id]

    def hpwl(self, x, y):
        """
        @brief compute total HPWL 
        @param x horizontal cell locations 
        @param y vertical cell locations 
        @return hpwl of all nets
        """
        wl = 0
        for net_id in range(len(self.net2pin_map)):
            wl += self.net_hpwl(x, y, net_id)
        return wl 

    def overlap(self, xl1, yl1, xh1, yh1, xl2, yl2, xh2, yh2):
        """
        @brief compute overlap between two boxes 
        @return overlap area between two rectangles
        """
        return max(min(xh1, xh2)-max(xl1, xl2), 0.0) * max(min(yh1, yh2)-max(yl1, yl2), 0.0)

    def density_map(self, x, y):
        """
        @brief this density map evaluates the overlap between cell and bins 
        @param x horizontal cell locations 
        @param y vertical cell locations 
        @return density map 
        """
        bin_index_xl = np.maximum(np.floor(x/self.bin_size_x).astype(np.int32), 0)
        bin_index_xh = np.minimum(np.ceil((x+self.node_size_x)/self.bin_size_x).astype(np.int32), self.num_bins_x-1)
        bin_index_yl = np.maximum(np.floor(y/self.bin_size_y).astype(np.int32), 0)
        bin_index_yh = np.minimum(np.ceil((y+self.node_size_y)/self.bin_size_y).astype(np.int32), self.num_bins_y-1)

        density_map = np.zeros([self.num_bins_x, self.num_bins_y])

        for node_id in range(self.num_physical_nodes):
            for ix in range(bin_index_xl[node_id], bin_index_xh[node_id]+1):
                for iy in range(bin_index_yl[node_id], bin_index_yh[node_id]+1):
                    density_map[ix, iy] += self.overlap(
                            self.bin_xl(ix), self.bin_yl(iy), self.bin_xh(ix), self.bin_yh(iy), 
                            x[node_id], y[node_id], x[node_id]+self.node_size_x[node_id], y[node_id]+self.node_size_y[node_id]
                            )

        for ix in range(self.num_bins_x):
            for iy in range(self.num_bins_y):
                density_map[ix, iy] /= (self.bin_xh(ix)-self.bin_xl(ix))*(self.bin_yh(iy)-self.bin_yl(iy))

        return density_map

    def density_overflow(self, x, y, target_density):
        """
        @brief if density of a bin is larger than target_density, consider as overflow bin 
        @param x horizontal cell locations 
        @param y vertical cell locations 
        @param target_density target density 
        @return density overflow cost 
        """
        density_map = self.density_map(x, y)
        return np.sum(np.square(np.maximum(density_map-target_density, 0.0)))

    def print_node(self, node_id): 
        """
        @brief print node information 
        @param node_id cell index 
        """
        logging.debug("node %s(%d), size (%g, %g), pos (%g, %g)" % (self.node_names[node_id], node_id, self.node_size_x[node_id], self.node_size_y[node_id], self.node_x[node_id], self.node_y[node_id]))
        pins = "pins "
        for pin_id in self.node2pin_map[node_id]:
            pins += "%s(%s, %d) " % (self.node_names[self.pin2node_map[pin_id]], self.net_names[self.pin2net_map[pin_id]], pin_id)
        logging.debug(pins)

    def print_net(self, net_id):
        """
        @brief print net information
        @param net_id net index 
        """
        logging.debug("net %s(%d)" % (self.net_names[net_id], net_id))
        pins = "pins "
        for pin_id in self.net2pin_map[net_id]:
            pins += "%s(%s, %d) " % (self.node_names[self.pin2node_map[pin_id]], self.net_names[self.pin2net_map[pin_id]], pin_id)
        logging.debug(pins)

    def print_row(self, row_id):
        """
        @brief print row information 
        @param row_id row index 
        """
        logging.debug("row %d %s" % (row_id, self.rows[row_id]))

    #def flatten_nested_map(self, net2pin_map): 
    #    """
    #    @brief flatten an array of array to two arrays like CSV format 
    #    @param net2pin_map array of array 
    #    @return a pair of (elements, cumulative column indices of the beginning element of each row)
    #    """
    #    # flat netpin map, length of #pins
    #    flat_net2pin_map = np.zeros(len(pin2net_map), dtype=np.int32)
    #    # starting index in netpin map for each net, length of #nets+1, the last entry is #pins  
    #    flat_net2pin_start_map = np.zeros(len(net2pin_map)+1, dtype=np.int32)
    #    count = 0
    #    for i in range(len(net2pin_map)):
    #        flat_net2pin_map[count:count+len(net2pin_map[i])] = net2pin_map[i]
    #        flat_net2pin_start_map[i] = count 
    #        count += len(net2pin_map[i])
    #    assert flat_net2pin_map[-1] != 0
    #    flat_net2pin_start_map[len(net2pin_map)] = len(pin2net_map)
     
    #    return flat_net2pin_map, flat_net2pin_start_map

    def read(self, params): 
        """
        @brief read using c++ 
        @param params parameters 
        """
        self.dtype = datatypes[params.dtype]
        self.rawdb = place_io.PlaceIOFunction.read(params)
        self.initialize_from_rawdb(params)

    def initialize_from_rawdb(self, params):
        """
        @brief initialize data members from raw database 
        @param params parameters 
        """
        pydb = place_io.PlaceIOFunction.pydb(self.rawdb)

        self.num_physical_nodes = pydb.num_nodes
        self.num_terminals = pydb.num_terminals
        self.num_terminal_NIs = pydb.num_terminal_NIs
        self.node_name2id_map = pydb.node_name2id_map
        self.node_names = np.array(pydb.node_names, dtype=np.string_)
        # If the placer directly takes a global placement solution, 
        # the cell positions may still be floating point numbers. 
        # It is not good to use the place_io OP to round the positions. 
        # Currently we only support BOOKSHELF format. 
        use_read_pl_flag = False 
        if (not params.global_place_flag) and os.path.exists(params.aux_input): 
            filename = None 
            with open(params.aux_input, "r") as f:
                for line in f:
                    line = line.strip()
                    if ".pl" in line: 
                        tokens = line.split()
                        for token in tokens:
                            if token.endswith(".pl"):
                                filename = token
                                break
            filename = os.path.join(os.path.dirname(params.aux_input), filename)
            if filename is not None and os.path.exists(filename):
                self.node_x = np.zeros(self.num_physical_nodes, dtype=self.dtype)
                self.node_y = np.zeros(self.num_physical_nodes, dtype=self.dtype)
                self.node_orient = np.zeros(self.num_physical_nodes, dtype=np.string_)
                self.read_pl(params, filename)
                use_read_pl_flag = True
        if not use_read_pl_flag:
            self.node_x = np.array(pydb.node_x, dtype=self.dtype)
            self.node_y = np.array(pydb.node_y, dtype=self.dtype)
            self.node_orient = np.array(pydb.node_orient, dtype=np.string_)
        self.node_size_x = np.array(pydb.node_size_x, dtype=self.dtype)
        self.node_size_y = np.array(pydb.node_size_y, dtype=self.dtype)
        self.node2orig_node_map = np.array(pydb.node2orig_node_map, dtype=np.int32)
        self.pin_direct = np.array(pydb.pin_direct, dtype=np.string_)
        self.pin_offset_x = np.array(pydb.pin_offset_x, dtype=self.dtype)
        self.pin_offset_y = np.array(pydb.pin_offset_y, dtype=self.dtype)
        self.net_name2id_map = pydb.net_name2id_map
        self.net_names = np.array(pydb.net_names, dtype=np.string_)
        self.net2pin_map = pydb.net2pin_map
        self.flat_net2pin_map = np.array(pydb.flat_net2pin_map, dtype=np.int32)
        self.flat_net2pin_start_map = np.array(pydb.flat_net2pin_start_map, dtype=np.int32)
        self.net_weights = np.array(pydb.net_weights, dtype=self.dtype)
        self.node2pin_map = pydb.node2pin_map
        self.flat_node2pin_map = np.array(pydb.flat_node2pin_map, dtype=np.int32)
        self.flat_node2pin_start_map = np.array(pydb.flat_node2pin_start_map, dtype=np.int32)
        self.pin2node_map = np.array(pydb.pin2node_map, dtype=np.int32)
        self.pin2net_map = np.array(pydb.pin2net_map, dtype=np.int32)
        self.rows = np.array(pydb.rows, dtype=self.dtype)
        self.regions = pydb.regions 
        for i in range(len(self.regions)):
            self.regions[i] = np.array(self.regions[i], dtype=self.dtype)
        self.flat_region_boxes = np.array(pydb.flat_region_boxes, dtype=self.dtype)
        self.flat_region_boxes_start = np.array(pydb.flat_region_boxes_start, dtype=np.int32)
        self.node2fence_region_map = np.array(pydb.node2fence_region_map, dtype=np.int32)
        self.xl = float(pydb.xl)
        self.yl = float(pydb.yl)
        self.xh = float(pydb.xh)
        self.yh = float(pydb.yh)
        self.row_height = float(pydb.row_height)
        self.site_width = float(pydb.site_width)
        self.num_movable_pins = pydb.num_movable_pins
        self.total_space_area = float(pydb.total_space_area)

        self.routing_grid_xl = float(pydb.routing_grid_xl) 
        self.routing_grid_yl = float(pydb.routing_grid_yl) 
        self.routing_grid_xh = float(pydb.routing_grid_xh) 
        self.routing_grid_yh = float(pydb.routing_grid_yh) 
        if pydb.num_routing_grids_x: 
            self.num_routing_grids_x = pydb.num_routing_grids_x 
            self.num_routing_grids_y = pydb.num_routing_grids_y 
            self.num_routing_layers = len(pydb.unit_horizontal_capacities)
            self.unit_horizontal_capacity = np.array(pydb.unit_horizontal_capacities, dtype=self.dtype).sum()
            self.unit_vertical_capacity = np.array(pydb.unit_vertical_capacities, dtype=self.dtype).sum()
            self.unit_horizontal_capacities = np.array(pydb.unit_horizontal_capacities, dtype=self.dtype)
            self.unit_vertical_capacities = np.array(pydb.unit_vertical_capacities, dtype=self.dtype)
            self.initial_horizontal_demand_map = np.array(pydb.initial_horizontal_demand_map, dtype=self.dtype).reshape((-1, self.num_routing_grids_x, self.num_routing_grids_y)).sum(axis=0)
            self.initial_vertical_demand_map = np.array(pydb.initial_vertical_demand_map, dtype=self.dtype).reshape((-1, self.num_routing_grids_x, self.num_routing_grids_y)).sum(axis=0)
        else:
            self.num_routing_grids_x = params.route_num_bins_x
            self.num_routing_grids_y = params.route_num_bins_y
            self.num_routing_layers = 1
            self.unit_horizontal_capacity = params.unit_horizontal_capacity
            self.unit_vertical_capacity = params.unit_vertical_capacity

        # convert node2pin_map to array of array 
        for i in range(len(self.node2pin_map)):
            self.node2pin_map[i] = np.array(self.node2pin_map[i], dtype=np.int32)
        self.node2pin_map = np.array(self.node2pin_map)

        # convert net2pin_map to array of array 
        for i in range(len(self.net2pin_map)):
            self.net2pin_map[i] = np.array(self.net2pin_map[i], dtype=np.int32)
        self.net2pin_map = np.array(self.net2pin_map)

    def __call__(self, params):
        """
        @brief top API to read placement files 
        @param params parameters 
        """
        tt = time.time()

        self.read(params)
        self.initialize(params)

        logging.info("reading benchmark takes %g seconds" % (time.time()-tt))

    def initialize(self, params):
        """
        @brief initialize data members after reading 
        @param params parameters 
        """

        # scale 
        # adjust scale_factor if not set 
        if params.scale_factor == 0.0 or self.site_width != 1.0:
            params.scale_factor = 1.0 / self.site_width
            logging.info("set scale_factor = %g, as site_width = %g" % (params.scale_factor, self.site_width))
        self.scale(params.scale_factor)

        content = """
================================= Benchmark Statistics =================================
#nodes = %d, #terminals = %d, # terminal_NIs = %d, #movable = %d, #nets = %d
die area = (%g, %g, %g, %g) %g
row height = %g, site width = %g
""" % (
                self.num_physical_nodes, self.num_terminals, self.num_terminal_NIs, self.num_movable_nodes, len(self.net_names), 
                self.xl, self.yl, self.xh, self.yh, self.area, 
                self.row_height, self.site_width
                )

        # set number of bins 
        # derive bin dimensions by keeping the aspect ratio 
        aspect_ratio = (self.yh - self.yl) / (self.xh - self.xl)
        num_bins_x = int(math.pow(2, max(np.ceil(math.log2(math.sqrt(self.num_movable_nodes / aspect_ratio))), 0)))
        num_bins_y = int(math.pow(2, max(np.ceil(math.log2(math.sqrt(self.num_movable_nodes * aspect_ratio))), 0)))
        self.num_bins_x = max(params.num_bins_x, num_bins_x)
        self.num_bins_y = max(params.num_bins_y, num_bins_y)
        # set bin size 
        self.bin_size_x = (self.xh-self.xl)/self.num_bins_x 
        self.bin_size_y = (self.yh-self.yl)/self.num_bins_y 

        # bin center array 
        self.bin_center_x = self.bin_centers(self.xl, self.xh, self.bin_size_x)
        self.bin_center_y = self.bin_centers(self.yl, self.yh, self.bin_size_y)

        content += "num_bins = %dx%d, bin sizes = %gx%g\n" % (self.num_bins_x, self.num_bins_y, self.bin_size_x/self.row_height, self.bin_size_y/self.row_height)

        # set num_movable_pins 
        if self.num_movable_pins is None:
            self.num_movable_pins = 0 
            for node_id in self.pin2node_map:
                if node_id < self.num_movable_nodes:
                    self.num_movable_pins += 1
        content += "#pins = %d, #movable_pins = %d\n" % (self.num_pins, self.num_movable_pins)
        # set total cell area 
        self.total_movable_node_area = float(np.sum(self.node_size_x[:self.num_movable_nodes]*self.node_size_y[:self.num_movable_nodes]))
        # total fixed node area should exclude the area outside the layout and the area of terminal_NIs  
        self.total_fixed_node_area = float(np.sum(
                np.maximum(
                    np.minimum(self.node_x[self.num_movable_nodes:self.num_physical_nodes - self.num_terminal_NIs] + self.node_size_x[self.num_movable_nodes:self.num_physical_nodes - self.num_terminal_NIs], self.xh)
                    - np.maximum(self.node_x[self.num_movable_nodes:self.num_physical_nodes - self.num_terminal_NIs], self.xl), 
                    0.0) * np.maximum(
                        np.minimum(self.node_y[self.num_movable_nodes:self.num_physical_nodes - self.num_terminal_NIs] + self.node_size_y[self.num_movable_nodes:self.num_physical_nodes - self.num_terminal_NIs], self.yh)
                        - np.maximum(self.node_y[self.num_movable_nodes:self.num_physical_nodes - self.num_terminal_NIs], self.yl), 
                        0.0)
                ))
        content += "total_movable_node_area = %g, total_fixed_node_area = %g, total_space_area = %g\n" % (self.total_movable_node_area, self.total_fixed_node_area, self.total_space_area)

        target_density = min(self.total_movable_node_area / self.total_space_area, 1.0)
        if target_density > params.target_density:
            logging.warn("target_density %g is smaller than utilization %g, ignored" % (params.target_density, target_density))
            params.target_density = target_density 
        content += "utilization = %g, target_density = %g\n" % (self.total_movable_node_area / self.total_space_area, params.target_density)

        # insert filler nodes 
        if params.enable_fillers: 
            # the way to compute this is still tricky; we need to consider place_io together on how to 
            # summarize the area of fixed cells, which may overlap with each other. 
            placeable_area = max(self.area - self.total_fixed_node_area, self.total_space_area)
            content += "use placeable_area = %g to compute fillers\n" % (placeable_area)
            self.total_filler_node_area = max(placeable_area*params.target_density-self.total_movable_node_area, 0.0)
            node_size_order = np.argsort(self.node_size_x[:self.num_movable_nodes])
            filler_size_x = np.mean(self.node_size_x[node_size_order[int(self.num_movable_nodes*0.05):int(self.num_movable_nodes*0.95)]])
            filler_size_y = self.row_height
            self.num_filler_nodes = int(round(self.total_filler_node_area/(filler_size_x*filler_size_y)))
            self.node_size_x = np.concatenate([self.node_size_x, np.full(self.num_filler_nodes, fill_value=filler_size_x, dtype=self.node_size_x.dtype)])
            self.node_size_y = np.concatenate([self.node_size_y, np.full(self.num_filler_nodes, fill_value=filler_size_y, dtype=self.node_size_y.dtype)])
        else:
            self.total_filler_node_area = 0 
            self.num_filler_nodes = 0
        content += "total_filler_node_area = %g, #fillers = %d, filler sizes = %gx%g\n" % (self.total_filler_node_area, self.num_filler_nodes, filler_size_x, filler_size_y)
        if params.routability_opt_flag: 
            content += "================================== routing information =================================\n"
            content += "routing grids (%d, %d)\n" % (self.num_routing_grids_x, self.num_routing_grids_y)
            content += "routing grid sizes (%g, %g)\n" % (self.routing_grid_size_x, self.routing_grid_size_y)
            content += "routing capacity H/V (%g, %g) per tile\n" % (self.unit_horizontal_capacity * self.routing_grid_size_y, self.unit_vertical_capacity * self.routing_grid_size_x)
        content += "========================================================================================"

        logging.info(content)

    def write(self, params, filename, sol_file_format=None):
        """
        @brief write placement solution
        @param filename output file name 
        @param sol_file_format solution file format, DEF|DEFSIMPLE|BOOKSHELF|BOOKSHELFALL
        """
        tt = time.time()
        logging.info("writing to %s" % (filename))
        if sol_file_format is None: 
            if filename.endswith(".def"): 
                sol_file_format = place_io.SolutionFileFormat.DEF 
            else:
                sol_file_format = place_io.SolutionFileFormat.BOOKSHELF

        # unscale locations 
        unscale_factor = 1.0/params.scale_factor
        if unscale_factor == 1.0:
            node_x = self.node_x
            node_y = self.node_y
        else:
            node_x = self.node_x * unscale_factor
            node_y = self.node_y * unscale_factor

        # Global placement may have floating point positions. 
        # Currently only support BOOKSHELF format. 
        # This is mainly for debug.  
        if not params.legalize_flag and not params.detailed_place_flag and sol_file_format == place_io.SolutionFileFormat.BOOKSHELF:
            self.write_pl(params, filename, node_x, node_y)
        else:
            place_io.PlaceIOFunction.write(self.rawdb, filename, sol_file_format, node_x, node_y)
        logging.info("write %s takes %.3f seconds" % (str(sol_file_format), time.time()-tt))

    def read_pl(self, params, pl_file):
        """
        @brief read .pl file
        @param pl_file .pl file
        """
        tt = time.time()
        logging.info("reading %s" % (pl_file))
        count = 0
        with open(pl_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("UCLA"):
                    continue
                # node positions
                pos = re.search(r"(\w+)\s+([+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?)\s+([+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?)\s*:\s*(\w+)", line)
                if pos:
                    node_id = self.node_name2id_map[pos.group(1)]
                    self.node_x[node_id] = float(pos.group(2))
                    self.node_y[node_id] = float(pos.group(6))
                    self.node_orient[node_id] = pos.group(10)
                    orient = pos.group(4)
        if params.scale_factor != 1.0:
            self.scale_pl(params.scale_factor)
        logging.info("read_pl takes %.3f seconds" % (time.time()-tt))

    def write_pl(self, params, pl_file, node_x, node_y):
        """
        @brief write .pl file
        @param pl_file .pl file 
        """
        tt = time.time()
        logging.info("writing to %s" % (pl_file))
        content = "UCLA pl 1.0\n"
        str_node_names = np.array(self.node_names).astype(np.str)
        str_node_orient = np.array(self.node_orient).astype(np.str)
        for i in range(self.num_movable_nodes):
            content += "\n%s %g %g : %s" % (
                    str_node_names[i],
                    node_x[i], 
                    node_y[i], 
                    str_node_orient[i]
                    )
        # use the original fixed cells, because they are expanded if they contain shapes 
        fixed_node_indices = list(self.rawdb.fixedNodeIndices())
        for i, node_id in enumerate(fixed_node_indices):
            content += "\n%s %g %g : %s /FIXED" % (
                    str(self.rawdb.nodeName(node_id)), 
                    float(self.rawdb.node(node_id).xl()), 
                    float(self.rawdb.node(node_id).yl()), 
                    "N" # still hard-coded 
                    )
        for i in range(self.num_movable_nodes + self.num_terminals, self.num_movable_nodes + self.num_terminals + self.num_terminal_NIs):
            content += "\n%s %g %g : %s /FIXED_NI" % (
                    str_node_names[i],
                    node_x[i], 
                    node_y[i], 
                    str_node_orient[i]
                    )
        with open(pl_file, "w") as f:
            f.write(content)
        logging.info("write_pl takes %.3f seconds" % (time.time()-tt))

    def write_nets(self, params, net_file):
        """
        @brief write .net file
        @param params parameters 
        @param net_file .net file 
        """
        tt = time.time()
        logging.info("writing to %s" % (net_file))
        content = "UCLA nets 1.0\n"
        content += "\nNumNets : %d" % (len(self.net2pin_map))
        content += "\nNumPins : %d" % (len(self.pin2net_map))
        content += "\n"

        for net_id in range(len(self.net2pin_map)):
            pins = self.net2pin_map[net_id]
            content += "\nNetDegree : %d %s" % (len(pins), self.net_names[net_id])
            for pin_id in pins: 
                content += "\n\t%s %s : %d %d" % (self.node_names[self.pin2node_map[pin_id]], self.pin_direct[pin_id], self.pin_offset_x[pin_id]/params.scale_factor, self.pin_offset_y[pin_id]/params.scale_factor)

        with open(net_file, "w") as f:
            f.write(content)
        logging.info("write_nets takes %.3f seconds" % (time.time()-tt))

    def apply(self, params, node_x, node_y):
        """
        @brief apply placement solution and update database 
        """
        # assign solution 
        self.node_x[:self.num_movable_nodes] = node_x[:self.num_movable_nodes]
        self.node_y[:self.num_movable_nodes] = node_y[:self.num_movable_nodes]

        # unscale locations 
        unscale_factor = 1.0/params.scale_factor
        if unscale_factor == 1.0:
            node_x = self.node_x
            node_y = self.node_y
        else:
            node_x = self.node_x * unscale_factor
            node_y = self.node_y * unscale_factor

        # update raw database 
        place_io.PlaceIOFunction.apply(self.rawdb, node_x, node_y)

    def is_node_a_standard_cell(self, node_index):
        return self.node_size_y[node_index] <= 2 * self.row_height
    
    def is_node_a_port(self, node_index):
        return self.node_size_x[node_index] == 0 and self.node_size_y[node_index] == 0
    
    def create_circuit_metagraph(self, filename):
        self.port_xh = (self.node_x[self.num_movable_nodes:self.num_movable_nodes + self.num_terminals] + self.node_size_x[self.num_movable_nodes:self.num_movable_nodes + self.num_terminals]).max()
        self.port_yh = (self.node_y[self.num_movable_nodes:self.num_movable_nodes + self.num_terminals] + self.node_size_y[self.num_movable_nodes:self.num_movable_nodes + self.num_terminals]).max()
        self.canvas_xh = max(self.port_xh, self.xh)
        self.canvas_yh = max(self.port_yh, self.yh)
        logging.info("canvas width is %g, height is %g" %(self.canvas_xh, self.canvas_yh))

        with open(filename, "w") as file:
            
            def add_input_fields(pin_index):
                if self.pin_direct[pin_index] == b'OUTPUT':
                    for pin in self.net2pin_map[self.pin2net_map[pin_index]]:
                        if self.pin_direct[pin] == b'INPUT':
                            parent_node_index = self.pin2node_map[pin]
                            if self.is_node_a_standard_cell(parent_node_index) or self.is_node_a_port(parent_node_index):
                                file.write(f'  input: "{self.node_names[parent_node_index].decode("utf-8")}"\n')
                            else:                            
                                file.write(f'  input: "pin_{pin}"\n')

            def convert_macro_pin(macro_index, pin_index):
                file.write('node {\n')
                file.write(f'  name: "pin_{pin_index}"\n')

                add_input_fields(pin_index)

                file.write('  attr {\n')
                file.write('    key: "type"\n')
                file.write('    value: {\n')
                file.write('      placeholder: "macro_pin"\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "macro_name"\n')
                file.write('    value: {\n')
                file.write(f'      placeholder: "{self.node_names[macro_index].decode("utf-8")}"\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "x_offset"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.pin_offset_x[pin_index] - self.node_size_x[macro_index] / 2}\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "y_offset"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.pin_offset_y[pin_index] - self.node_size_y[macro_index] / 2}\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('}\n')

            def convert_a_macro(node_index, fixed=False):
                file.write('node {\n')
                file.write(f'  name: "{self.node_names[node_index].decode("utf-8")}"\n')

                file.write('  attr {\n')
                file.write('    key: "type"\n')
                file.write('    value: {\n')
                file.write('      placeholder: "macro"\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "width"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.node_size_x[node_index]}\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "height"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.node_size_y[node_index]}\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "x"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.node_x[node_index] + self.node_size_x[node_index] / 2}\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "y"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.node_y[node_index] + self.node_size_y[node_index] / 2}\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "orientation"\n')
                file.write('    value: {\n')
                file.write(f'      placeholder: "{self.node_orient[node_index].decode("utf-8")}"\n')
                file.write('    }\n')
                file.write('  }\n')

                if fixed:
                    file.write('  attr {\n')
                    file.write('    key: "fixed"\n')
                    file.write('    value: {\n')
                    file.write(f'      b: true\n')
                    file.write('    }\n')
                    file.write('  }\n')

                file.write('}\n')

                pin_lists = self.node2pin_map[node_index]
                for pin_index in pin_lists:
                    convert_macro_pin(node_index, pin_index)
                
            def convert_a_standard_cell(node_index):
                file.write('node {\n')
                file.write(f'  name: "{self.node_names[node_index].decode("utf-8")}"\n')

                for pin_index in self.node2pin_map[node_index]:
                    add_input_fields(pin_index)

                file.write('  attr {\n')
                file.write('    key: "type"\n')
                file.write('    value: {\n')
                file.write('      placeholder: "stdcell"\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "width"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.node_size_x[node_index]}\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "height"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.node_size_y[node_index]}\n')
                file.write('    }\n')
                file.write('  }\n')
                
                file.write('  attr {\n')
                file.write('    key: "x"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.node_x[node_index] + self.node_size_x[node_index] / 2}\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "y"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.node_y[node_index] + self.node_size_y[node_index] / 2}\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('}\n')

            def convert_a_port(node_index):
                file.write('node {\n')
                file.write(f'  name: "{self.node_names[node_index].decode("utf-8")}"\n')

                for pin_index in self.node2pin_map[node_index]:
                    add_input_fields(pin_index)

                file.write('  attr {\n')
                file.write('    key: "type"\n')
                file.write('    value: {\n')
                file.write('      placeholder: "port"\n')
                file.write('    }\n')
                file.write('  }\n')
                
                file.write('  attr {\n')
                file.write('    key: "x"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.node_x[node_index]}\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('  attr {\n')
                file.write('    key: "y"\n')
                file.write('    value: {\n')
                file.write(f'      f: {self.node_y[node_index]}\n')
                file.write('    }\n')
                file.write('  }\n')
                
                if self.node_x[node_index] == 0:
                    side = "left"
                elif self.node_x[node_index] == self.canvas_xh:
                    side = "right"
                elif self.node_y[node_index] == 0:
                    side = "bottom"
                elif self.node_y[node_index] == self.canvas_yh:
                    side = "top"
                else:
                    logging.info("Port %s with location (%g. %g) is not at the chip boundary" % (self.node_names[node_index].decode("utf-8"), self.node_x[node_index], self.node_y[node_index]))
                    file.write('}\n')
                    return

                file.write('  attr {\n')
                file.write('    key: "side"\n')
                file.write('    value: {\n')
                file.write(f'      placeholder: "{side}"\n')
                file.write('    }\n')
                file.write('  }\n')

                file.write('}\n')
                return

            file.write(f'# FP bbox: {{0.0 0.0}} {{{self.canvas_xh} {self.canvas_yh}}}\n')
            if self.yl > 0:
                file.write(f'# Blockage : 0.0 0.0 {self.canvas_xh} {self.yl} 1.0\n')
            if self.canvas_yh > self.yh:
                file.write(f'# Blockage : 0.0 {self.yh} {self.canvas_xh} {self.canvas_yh} 1.0\n')
            if self.xl > 0:                
                file.write(f'# Blockage : 0.0 {self.yl} {self.xl} {self.yh} 1.0\n')
            if self.canvas_xh > self.xh:
                file.write(f'# Blockage : {self.xh} {self.yl} {self.canvas_xh} {self.yh} 1.0\n')

            # Convert movable nodes (standard cells and movable macros)
            for i in range(self.num_movable_nodes):
                if self.is_node_a_standard_cell(i):
                    convert_a_standard_cell(i)
                else:
                    convert_a_macro(i, fixed=False)
            
            # Convert fixed nodes (fixed macros and IO ports)
            for i in range(self.num_movable_nodes, self.num_movable_nodes + self.num_terminals):
                if self.is_node_a_port(i):
                    convert_a_port(i)
                else:
                    convert_a_macro(i, fixed=True)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        logging.error("One input parameters in json format in required")

    params = Params.Params()
    params.load(sys.argv[sys.argv[1]])
    logging.info("parameters = %s" % (params))

    db = PlaceDB()
    db(params)

    db.print_node(1)
    db.print_net(1)
    db.print_row(1)
