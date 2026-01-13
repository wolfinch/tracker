#! /usr/bin/env python3
'''
# tracker Stock Tracker
# Desc: Main File implements Tracker Entry points
#  Copyright: (c) 2017-2026 tracker Inc.
#  This file is part of tracker.
# 
#  tracker is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
# 
#  tracker is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
# 
#  You should have received a copy of the GNU General Public License
#  along with tracker.  If not, see <https://www.gnu.org/licenses/>.
'''

import sys
import os

from strategies.tracker_base import Tstats
sys.path.append(os.path.join(os.path.abspath(os.path.dirname(sys.argv[0])), "../tracker/pkgs"))
sys.path.append(os.path.join(os.path.abspath(os.path.dirname(sys.argv[0])), "../tracker/exchanges"))

import time
import traceback
import argparse
from decimal import getcontext
import random
import logging
from  strategies import Configure
# import notifiers
import tdata
import ui
import gc

from utils import getLogger, readConf
from db import TrackerDb, clear_db

log = getLogger("Tracker")
log.setLevel(logging.ERROR)

# mpl_logger = logging.getLogger('matplotlib')
# mpl_logger.setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(log.WARNING)

TrackerConfig = None
ticker_import_time = 0

# global Variables
MAIN_TICK_DELAY = 1  # 500*4 milli

def tracker_init():
    global TrackerConfig
    # seed random
    random.seed()

    #init data source
    tdata.init()

    # print ("config: %s"%(TrackerConfig))
    notifier = TrackerConfig.get("notifier")
    if notifier != None:
        if False == notifiers.init(notifier):
            log.critical("notifier init failed")
            return False
    register_trackers(TrackerConfig.get("strategies"))
    
    # setup ui if required
    if TrackerConfig["ui"]["enabled"]:
        log.info("ui init")
        if False == ui.ui_init(port=TrackerConfig["ui"].get("port"), get_data_cb=get_tracker_data) :
            log.critical("unable to setup ui!! ")
            print("unable to setup UI!!")
            sys.exit(1)

def tracker_end():
    log.info("Finalizing Tracker")

    # stop stats thread
    log.info("waiting to stop stats thread")
    # notifiers.end()
    ui.ui_end()
    log.info("all cleanup done.")

def tracker_main():
    """
    Main Function for Tracker
    """
    sleep_time = MAIN_TICK_DELAY
    gc_time = 0
    while True:
        cur_time = time.time()
        update_data()
        process_trackers()
        if gc_time + 6*60*60 < int(time.time()):
            log.info("force garbage collect")
            gc.collect()
            gc_time = int(time.time())
        # '''Make sure each iteration take exactly LOOP_DELAY time'''
        sleep_time = (MAIN_TICK_DELAY -(time.time()- cur_time))
#         if sleep_time < 0 :
#             log.critical("******* TIMING SKEWED(%f)******"%(sleep_time))
        sleep_time = 0 if sleep_time < 0 else sleep_time
        time.sleep(sleep_time)
    # end While(true)

g_trackers = []
g_ticker_stats = {}
def register_trackers(cfg):
    global g_trackers
    log.debug("registering trackers")
    g_trackers = Configure(cfg)
    for scrn_obj in g_trackers:
        #create data holders for each tracker
        db = TrackerDb(Tstats, scrn_obj.name)
        t_stats = db.db_get_data()
        g_ticker_stats[scrn_obj.name] = Tstats(t_stats.data or {})
        g_ticker_stats[scrn_obj.name].db = db
        g_ticker_stats[scrn_obj.name].updated = scrn_obj.updated = t_stats.updated
        g_ticker_stats[scrn_obj.name].update_time = scrn_obj.update_time = t_stats.update_time
def update_data():
    #update stats only during ~12hrs, to cover pre,open,ah
    log.debug("updating data")
    sym_list = get_all_tickers()
    for scrn_obj in g_trackers:
        if scrn_obj.interval + scrn_obj.update_time < int(time.time()):
            s_list = sym_list.get(scrn_obj.ticker_kind)
            if not s_list :
                log.critical("unable to find ticker list kind %s"%(scrn_obj.ticker_kind))
                continue
            log.info ("updating tracker data for %s num_sym: %d"%(scrn_obj.name, len(s_list)))                
            if scrn_obj.update(s_list, g_ticker_stats):
                scrn_obj.updated = True
                g_ticker_stats[scrn_obj.name].updated = True
                #update time. 
                # Sometimes, data not updated during market close etc. handle this in tracker, update routine 
                scrn_obj.update_time = int(time.time())                
                g_ticker_stats[scrn_obj.name].update_time = scrn_obj.update_time
                g_ticker_stats[scrn_obj.name].db.db_save_data(g_ticker_stats[scrn_obj.name])
                log.info("tracker data %s saved to db "%(scrn_obj.name))
            else:
                g_ticker_stats[scrn_obj.name].updated = False

def process_trackers ():
    log.debug("processing trackers")
    sym_list = get_all_tickers()
    for scrn_obj in g_trackers:
        if scrn_obj.updated :
            s_list = sym_list.get(scrn_obj.ticker_kind)
            if not s_list :
                log.critical("unable to find ticker list kind %s"%(scrn_obj.ticker_kind))
                continue            
            log.info ("running tracker - %s sym_num: %d"%(scrn_obj.name, len(s_list)))
            scrn_obj.screen(s_list, g_ticker_stats)
            scrn_obj.updated = False
            
def get_all_tracker_data():
    #run thru all trackers and collect filtered data
    filtered_list = {}
    for scrn_obj in g_trackers:
        log.info("get tracker data from %s"%(scrn_obj.name))
        filtered_list[scrn_obj.name] = scrn_obj.get_screened()
    return filtered_list

all_tickers = {"ALL":[], "MEGACAP":[], "GT50M": [], "LT50M": [], "OTC": [],
               "ALL500K":[], "MEGACAP500K":[], "GT50M500K": [], "LT50M500K": [], "OTC500K": [], "SPAC": []}
def get_all_tickers ():
    global ticker_import_time, all_tickers
    log.debug ("get all tickers")
    if ticker_import_time + 24*3600 < int(time.time()) :
        all_tickers = tdata.get_all_ticker_lists()
    return all_tickers
    
def get_tracker_data():
#     log.info("msg %s"%(msg))
    data_set = get_all_tracker_data()
    return data_set

def clean_states():
    ''' 
    clean states
    '''
    log.info("Clearing Db")
    clear_db()
    
def arg_parse():
    '''
    arg parse
    '''
    parser = argparse.ArgumentParser(description='tracker Tracker')

    parser.add_argument('--version', action='version', version='%(prog)s 1.0.1')
    parser.add_argument("--clean",
                        help='Clean states,dbs and exit. Clear all the existing states',
                        action='store_true')
    parser.add_argument("--config", help='tracker Tracker config file')    
    parser.add_argument("--port", help='API Port')
    parser.add_argument("--restart", help='restart from the previous state', action='store_true')

    args = parser.parse_args()
    
    # if args.config:
    #     log.debug("config file: %s" % (str(args.config)))

    # else:
    #     parser.print_help()
    #     exit(1)    

    if args.clean:
        clean_states()
        exit(0)

    if args.port:
        log.debug("port: %s" % (str(args.port)))
        ui.port = args.port
    else:
        pass
#         parser.print_help()
#         exit(1)

    if args.restart:
        log.debug("restart enabled")
        print("Restarting from previous state")
    else:
        log.debug("restart disabled")

######### ******** MAIN ****** #########
if __name__ == '__main__':
    '''
    main entry point
    '''
    arg_parse()
    getcontext().prec = 8  # decimal precision
    print("Starting tracker Tracker..")
    try:
        tracker_init()
        log.info("Starting Main forever loop")
        print("Starting Main forever loop")
        tracker_main()
    except(KeyboardInterrupt, SystemExit):
        tracker_end()
        sys.exit()
    except Exception as e:
        log.critical("Unexpected error: exception: %s" %(traceback.format_exc()))
        print("Unexpected error: exception: %s" %(traceback.format_exc()))
        tracker_end()
        raise
#         traceback.print_exc()
#         os.abort()
    # '''Not supposed to reach here'''
    print("\nTracker end")

# EOF