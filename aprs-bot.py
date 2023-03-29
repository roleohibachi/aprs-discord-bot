import os
import re
import time
from datetime import datetime
import sys
import argparse
import traceback
import logging
import aprslib
from discordwebhook import Discord


def send_aprs_msg(AIS:  aprslib.IS, fromCall: str, toCall: str, message: str, lineNo: int): 
    message=re.sub(r'[{:]','',message)
    pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":"+message+"{"+str(lineNo)
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("[DEBUG] Simulated send: "+pkt)
    else:
        sent=AIS.sendall(pkt)
        logging.info("Sent: "+pkt)
    return lineNo + 1

def send_aprs_ack(AIS: aprslib.IS, toCall: str, msgNo: int, fromCall: str):
    pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":ack"+str(msgNo)
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.debug("[DEBUG] Simulated ACK: "+pkt)
    else:
        sent=AIS.sendall(pkt)
        #todo: wait 30 and double-tap
        print("Sent ACK: "+pkt)

from collections import Mapping
class TenRingDict(dict):
    def __init__(self, other=None, **kwargs):
        super().__init__()
        self.update(other, **kwargs)

    def __setitem__(self, key, value):
        if len(self)>=10:
            self.pop(next(iter(self)))
        super().__setitem__(key, value)

    def update(self, other=None, **kwargs):
        if other is not None:
            for k, v in other.items() if isinstance(other, Mapping) else other:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

def main():
    parser = argparse.ArgumentParser(description='Bridge between APRS and Discord.')

    parser.add_argument( '-log',
        '--loglevel',
        default='warning',
        help='Provide logging level. Example --loglevel debug, default=warning' )

    parser.add_argument( '-bot','--botName', default="aprsbot", help='Username for the bot to use in Discord')
    parser.add_argument( '--botSecret', default=os.environ.get('DISCORD_WEBHOOK_URL'), help='Discord bot secret.')
    parser.add_argument( '--botCall', default=os.environ.get('DISCORD_BOT_CALL'), help='Callsign for the bot to use on APRS')
    parser.add_argument( '--botSSID', default=os.environ.get('DISCORD_BOT_SSID'), help='SSID for the bot to use on APRS. Useful for multiple discord channels. Include the leading dash.')
    parser.add_argument( '--botChannelID', type=int, default=int(os.environ.get('DISCORD_BOT_CHANNEL')), help='Discord channel ID to bridge.')
    parser.add_argument( '--adminCall', default=os.environ.get('APRS_CALL'), help='Callsign to authenticate with APRS. Under whose license are you transmitting?')
    parser.add_argument( '--adminSSID', default=os.environ.get('APRS_SSID'), help='SSID for the admin, who will receive APRS status updates.')
    parser.add_argument( '--adminPass', default=os.environ.get('APRS_PASSWD'), help='Password for the APRS user.')
    parser.add_argument( '--aprsHost', default="noam.aprs2.net", help='APRS-IS server')
    parser.add_argument( '--aprsPort', type=int, default=14580, help='APRS-IS port')
    parser.add_argument( '--aprsMsgNo', type=int, default=int(time.time()/10%(pow(10,2))), help='The initial serialized message number. If unset, will be random.')

    args = parser.parse_args()
    logging.basicConfig( level=args.loglevel.upper() )
    aprsMsgNo=args.aprsMsgNo

    lastHeard = TenRingDict()

    #configure APRS
    AIS = aprslib.IS(args.adminCall,args.adminPass,host=args.aprsHost,port=args.aprsPort)
    AIS.set_filter("g/"+args.botCall)
    
    #configure Discord
    discord = Discord(url=args.botSecret)

    def aprs_handler(packet):
        if 'format' in packet and packet['format'] == "message":
            if 'response' in packet and packet['response'] == "ack":
                logging.debug("Got an ACK for message "+packet['msgNo'])
            elif 'message_text' in packet:
                logging.debug("Got a message! Here it is: "+packet['from'] + ": " + packet['message_text']+"... msgno "+packet['msgNo'])
                if not packet['from'] in lastHeard:
                    lastHeard.update({packet['from']:'0'})
                if int(packet['msgNo']) > int(lastHeard[packet['from']]):
                    embed={
                                "title": packet['from']+": ",
                                "type": "rich",
                                "description": packet['message_text'],
                                "url": "https://aprs.fi/?c=raw&call="+packet['from'],
                                "timestamp": str(datetime.now()),
                                "footer": {
                                    "text": "Licensed radio amateurs can post to this channel by sending APRS messages to callsign 'PPRAA' with standard message format.",
                                },
                                "fields": [
                                    {"name": "via", "value": packet['via'], "inline": True},
                                    {"name": "msgNo", "value": packet['msgNo'], "inline": True},
                                ],
                            }
    
                discord.post(username=args.botName,embeds=[embed])
                send_aprs_ack(AIS,fromCall=args.botCall,toCall=packet['from'],msgNo=packet['msgNo'])
                lastHeard.update({packet['from']:packet['msgNo']})

            else:
                logging.debug('Heard this one before - not posting, repeating ACK')
                send_aprs_ack(AIS,fromCall=args.botCall,toCall=packet['from'],msgNo=packet['msgNo'])

    try:

        AIS.connect()
        aprsMsgNo = send_aprs_msg(AIS,fromCall=args.botCall,toCall=args.adminCall+args.adminSSID,message="script online",lineNo=aprsMsgNo)
        discord.post(content="Bot online! Send an APRS message to PPRAA to have it posted here.",username=args.botName)

        # by default `raw` is False, then each line is ran through aprslib.parse()
        AIS.consumer(aprs_handler, raw=False)

        #we should never make it to here
        AIS.close()

    except KeyboardInterrupt:
        print("Shutdown requested... notifying admin")
        aprsMsgNo = send_aprs_msg(AIS,fromCall=args.botCall,toCall=args.adminCall+args.adminSSID,message="script offline",lineNo=aprsMsgNo)
        discord.post(content="Bot is now offline.",username=args.botName)
        AIS.close()
    except Exception as err:
        AIS.close()
        print(traceback.format_exc())
        sys.exit(1)
    
    sys.exit(0)

if __name__ == "__main__":
    main()
