from collections import namedtuple

import numpy as np
import pandas as pd
import ccxt
import re
import json
import sys
from os import path
import os

# ======================================================================
# DataModule is responsible for downloading OHLCV data, preparing it
# and activating backtesting methods
#
# © 2021 DemaTrading.AI
# ======================================================================

msec = 1000
minute = 60 * msec
hour = 60 * minute
day = 24 * hour


class DataModule:
    exchange = None
    timeframe_calc = None

    backtesting_from = None
    backtesting_to = None

    history_data = {}

    def __init__(self, config, backtesting_module):
        print('[INFO] Starting DEMA Data-module...')
        self.config = config
        self.backtesting_module = backtesting_module
        self.config_timeframe_calc()
        self.load_exchange()

    def load_exchange(self) -> None:
        """
        Method checks for requested exchange existence
        checks for exchange OHLCV compatibility
        checks for timeframe support
        loads markets if no errors occur
        :return: None
        :rtype: None
        """
        print('[INFO] Connecting to exchange...')
        exchange_id = self.config['exchange']

        # Try to get exchange based on config param 'exchange'
        try:
            self.exchange = getattr(ccxt, exchange_id)
            self.exchange = self.exchange()
            print("[INFO] Connected to exchange: %s." % self.config['exchange'])
        except AttributeError:
            print("[ERROR] Exchange %s could not be found!" % exchange_id)
            raise SystemExit

        # Check whether exchange supports OHLC
        if not self.exchange.has["fetchOHLCV"]:
            print("[ERROR] Cannot load data from %s because it doesn't support OHLCV-data" % self.config['exchange'])
            raise SystemExit

        # Check whether exchange supports requested timeframe
        if (not hasattr(self.exchange, 'timeframes')) or (self.config['timeframe'] not in self.exchange.timeframes):
            print("[ERROR] Requested timeframe is not available from %s" % self.config['exchange'])
            raise SystemExit

        self.load_markets()

    def load_markets(self) -> None:
        self.exchange.load_markets()
        self.config_from_to()
        self.load_historical_data()

    def load_historical_data(self) -> None:
        """
        Method checks for datafile existence, if not existing, download data and save to file
        :return: None
        :rtype: None
        """
        for pair in self.config['pairs']:
            if not self.check_datafolder(pair):
                print("[INFO] Did not find datafile for %s" % pair)
                df = self.download_data_for_pair(pair, self.backtesting_from, self.backtesting_to)
            else:
                print("[INFO] Reading datafile for %s" % pair)
                df = self.read_data_from_datafile(pair)
            self.history_data[pair] = df

        self.backtesting_module.start_backtesting(self.history_data, self.backtesting_from, self.backtesting_to)

    def download_data_for_pair(self, pair: str, data_from: str, data_to: str, save: bool = True) -> pd.DataFrame:
        """
        :param pair: Certain coin pair in "AAA/BBB" format
        :type pair: string
        :param data_from: Starting point for collecting data
        :type data_from: string
        :param data_to: Ending point for collecting data
        :type data_from: string
        :return: None
        :rtype: None
        """
        start_date = data_from
        fetch_ohlcv_limit = 1000

        if save:
            print("[INFO] Downloading %s's data" % pair)

        index, ohlcv_data = [], []
        while start_date < data_to:
            # Request ticks for given pair (maximum = 1000)
            remaining_ticks = (data_to - start_date) / self.timeframe_calc
            asked_ticks = min(remaining_ticks, fetch_ohlcv_limit)
            result = self.exchange.fetch_ohlcv(symbol=pair, timeframe=self.config['timeframe'], \
                                                since=int(start_date), limit=int(asked_ticks))

            # Save timestamps and ohlcv info
            index += [candle[0] for candle in result]   # timestamps
            ohlcv_data += result
            start_date += np.around(asked_ticks * self.timeframe_calc)

        # Create pandas DataFrame and add pair info
        df = pd.DataFrame(ohlcv_data, index=index, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df['pair'] = pair
        if save:
            print("[INFO] [%s] %s candles downloaded" % (pair, len(index)))
            self.save_dataframe(pair, df)
        return df

    def config_timeframe_calc(self) -> None:
        """
        Method checks for valid timeframe input in config file.
        Besides sets self.timeframe_calc property, which is used
        to calculate how much time has passed based on an amount of candles
        so:
        self.timeframe_calc * 10 candles passed = milliseconds passed
        """
        print('[INFO] Configuring timeframe')

        timeframe = self.config['timeframe']
        match = re.match(r"([0-9]+)([mdh])", timeframe, re.I)
        if not match:
            print("[ERROR] Error whilst parsing timeframe")
            raise SystemExit
        items = re.split(r'([0-9]+)', timeframe)
        if items[2] == 'm':
            self.timeframe_calc = int(items[1]) * minute
        elif items[2] == 'h':
            self.timeframe_calc = int(items[1]) * hour

    def config_from_to(self) -> None:
        """
        This method sets the self.backtesting_to / backtesting_from properties
        with 8601 parsed timestamp
        :return: None
        :rtype: None
        """
        test_from = self.config['backtesting-from']
        test_to = self.config['backtesting-to']
        test_till_now = self.config['backtesting-till-now']

        self.backtesting_from = self.exchange.parse8601("%sT00:00:00Z" % test_from)
        if test_till_now == 'True':
            print('[INFO] Gathering data from %s until now' % test_from)
            self.backtesting_to = self.exchange.milliseconds()
        elif test_till_now == 'False':
            print('[INFO] Gathering data from %s until %s' % (test_from, test_to))
            self.backtesting_to = self.exchange.parse8601("%sT00:00:00Z" % test_to)
        else:
            print(
                "[ERROR] Something went wrong parsing config. Please use yyyy-mm-dd format at 'backtesting-from', 'backtesting-to'")

    def check_datafolder(self, pair: str) -> bool:
        """
        :param pair: Certain coin pair in "AAA/BBB" format
        :type pair: string
        :return: Returns whether datafile for specified pair / timeframe already exists
        :rtype: boolean
        """
        # Check if datafolder exists
        filename = self.generate_datafile_name(pair)
        exchange_path = os.path.join("data/backtesting-data", self.config["exchange"])
        if not path.exists(exchange_path):
            self.create_directory(exchange_path)

        # Checks if datafile exists
        dirpath = os.path.join(exchange_path, filename)
        return path.exists(dirpath)

    def create_directory(self, directory: str) -> None:
        """
        :param directory: string of path to directory
        :type directory: string
        :return: None
        :rtype: None
        """
        try:
            os.makedirs(directory)
        except OSError:
            print("Creation of the directory %s failed" % path)
        else:
            print("Successfully created the directory %s " % path)

    def read_data_from_datafile(self, pair: str) -> pd.DataFrame:
        """
        When datafile is covering requested backtesting period,
        this method reads the data from the files. Saves this in
        self.historical_data
        :param pair: Certain coin pair in "AAA/BBB" format
        :type pair: string
        :return: None
        :rtype: None
        """
        filename = self.generate_datafile_name(pair)
        filepath = os.path.join("data/backtesting-data/", self.config["exchange"], filename)
        try:
            with open(filepath, 'r') as datafile:
                data = datafile.read()
        except FileNotFoundError:
            print("[ERROR] Backtesting datafile was not found.")
            return False
        except:
            print("[ERROR] Something went wrong loading datafile", sys.exc_info()[0])
            return False

        # Convert json to dataframe
        json_file = json.loads(data)
        df = pd.read_json(json_file, orient="records")
        df.set_index('time', drop=False, inplace=True)

        # Get backtesting period
        index_list = list(df.index.values)
        df_begin, df_end = index_list[0], index_list[-1]
        final_timestamp = self.backtesting_to - self.timeframe_calc   # correct final timestamp
        extra_candles = 0

        # Check if previous data needs to be downloaded
        if self.backtesting_from < df_begin:
            prev_df = self.download_data_for_pair(pair, self.backtesting_from, df_begin, False)
            df = pd.concat([prev_df, df])
            extra_candles += len(prev_df.index)
        
        # Check if new data needs to be downloaded
        if final_timestamp > df_end:
            new_df = self.download_data_for_pair(pair, df_end, self.backtesting_to, False)
            df = pd.concat([df, new_df])
            extra_candles += len(new_df.index)

        # Check if new candles were downloaded
        if extra_candles > 0:
            print("[INFO] [%s] %s extra candle(s) downloaded" % (pair, extra_candles))
        
        # Slice correct backtesting period and save df
        index_list = list(df.index.values)                      # update index list
        df = df[index_list.index(self.backtesting_from):index_list.index(final_timestamp)+1]
        self.save_dataframe(pair, df)
        return df

    def save_dataframe(self, pair: str, df: pd.DataFrame) -> None:
        """
        Method creates new json datafile for pair in timeframe
        :param pair: Certain coin pair in "AAA/BBB" format
        :type pair: string
        :param df: Downloaded data to write to the datafile
        :type data: pd.DataFrame
        :return: None
        :rtype: None
        """
        filename = self.generate_datafile_name(pair)
        filepath = os.path.join("data/backtesting-data/", self.config["exchange"], filename)

        # Convert pandas dataframe to json and saves file
        df_json = df.to_json(orient="records")
        with open(filepath, 'w') as outfile:
            json.dump(df_json, outfile)

    def generate_datafile_name(self, pair: str) -> str:
        """
        :param pair: Certain coin pair in "AAA/BBB" format
        :type pair: string
        :return: returns a filename for specified pair / timeframe
        :rtype: string
        """
        coin, base = pair.split('/')
        return "data-{}{}{}.json".format(coin, base, self.config['timeframe'])

    def remove_backtesting_file(self, pair: str):
        """
        Method removes existing datafile, as it does not cover requested
        backtesting period.
        :param pair: Certain coin pair in "AAA/BBB" format
        :type pair: string
        :return: None
        :rtype: None
        """
        filename = self.generate_datafile_name(pair)
        filepath = os.path.join("data/backtesting-data/", self.config["exchange"], filename)
        os.remove(filepath)