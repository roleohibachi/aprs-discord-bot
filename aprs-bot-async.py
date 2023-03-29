import os
import re
import time
from datetime import datetime
import sys
import argparse
import traceback
import logging
import aprslib
import discord
from discord.ext import commands
import asyncio

async def send_aprs_msg(AIS:  aprslib.IS, fromCall: str, toCall: str, message: str, lineNo: int): 
    message=re.sub(r'[{:]','',message)
    pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":"+message+"{"+str(lineNo)
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.info("Simulated send: "+pkt)
    else:
        sent=AIS.sendall(pkt)
        logging.info("Sent: "+pkt)
    return lineNo + 1

async def send_aprs_ack(AIS: aprslib.IS, toCall: str, msgNo: int, fromCall: str):
    pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":ack"+str(msgNo)
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.info("[DEBUG] Simulated ACK: "+pkt)
        await asyncio.sleep(30)
        logging.info("[DEBUG] Simulated ACK (double-tap): "+pkt)
    else:
        logging.info("Sending ACK: "+pkt)
        sent=AIS.sendall(pkt)
        await asyncio.sleep(30)
        logging.info("Sending ACK (double-tap): "+pkt)
        sent=AIS.sendall(pkt)

class DiscordClient(discord.Client):
    async def on_ready(self):
        print(f'Logged on as {self.user}!')

    async def on_message(self, message):
        print(f'Message from {message.author}: {message.content}')
        if message.author == myDiscordClient.user:
            return
        if message.content.startswith('$hello'):
            await message.channel.send('Hello!')

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

class APRSClient():
    def __init__(self, *args, **kwargs):
            
async def bridge(DiscordClient,APRSClient):

async def main():
    parser = argparse.ArgumentParser(description='Bridge between APRS and Discord.')

    parser.add_argument( '-log',
        '--loglevel',
        default='warning',
        help='Provide logging level. Example --loglevel debug, default=warning' )
    
    parser.add_argument( '-bot','--botName', default="aprsbot", help='Username for the bot to use in Discord')
    parser.add_argument( '--botSecret', default=os.environ.get('DISCORD_BOT_SECRET'), help='Discord bot secret.')
    parser.add_argument( '--botCall', default=os.environ.get('DISCORD_BOT_CALL'), help='Callsign for the bot to use on APRS')
    parser.add_argument( '--botSSID', default=os.environ.get('DISCORD_BOT_SSID'), help='SSID for the bot to use on APRS. Useful for multiple discord channels. Include the leading dash.')
    parser.add_argument( '--botChannelID', type=int, default=int(os.environ.get('DISCORD_BOT_CHANNEL')), help='Discord channel ID to bridge.')
    parser.add_argument( '--adminCall', default=os.environ.get('APRS_CALL'), help='Callsign to authenticate with APRS. Under whose license are you transmitting?')
    parser.add_argument( '--adminPass', default=os.environ.get('APRS_PASSWD'), help='Password for the APRS user.')
    parser.add_argument( '--aprsHost', default="noam.aprs2.net", help='APRS-IS server')
    parser.add_argument( '--aprsPort', type=int, default=14580, help='APRS-IS port')
    parser.add_argument( '--aprsMsgNo', type=int, default=int(time.time()/10%(pow(10,2))), help='The initial serialized message number. If unset, will be random.')

    args = parser.parse_args()
    logging.basicConfig( level=args.loglevel.upper() )
    aprsMsgNo=args.aprsMsgNo

    lastHeard = TenRingDict()

    #configure Discord
    intents = discord.Intents.default()
    intents.message_content = True
    myDiscordClient = DiscordClient(intents=intents)

    #configure APRS
    AIS = aprslib.IS(args.adminCall,args.adminPass,host=args.aprsHost,port=args.aprsPort)
    AIS.set_filter("g/"+args.botCall)

    try:
        await myDiscordClient.start(args.botSecret)
        #await myDiscordClient.wait_until_ready()
        discordChannel = myDiscordClient.get_channel(int(args.botChannelID))

        await discordChannel.send("I'm online")

        async def aprs_handler(packet):
            try:
                packet = aprslib.parse(packet)
                if 'format' in packet and packet['format'] == "message":
                    if 'response' in packet and packet['response'] == "ack":
                        logging.debug("Got an ACK for message "+packet['msgNo'])
                    elif 'message_text' in packet:
                        logging.debug("Got a message! Here it is: "+packet['from'] + ": " + packet['message_text']+"... msgno "+packet['msgNo'])
                        if not packet['from'] in lastHeard:
                            lastHeard.update({packet['from']:'0'})
                        if int(packet['msgNo']) > int(lastHeard[packet['from']]):
                            embed=discord.Embed.from_dict({
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
                                    })
                            #ackTask = loop.create_task(send_aprs_ack(AIS,fromCall=args.botCall,toCall=packet['from'],msgNo=packet['msgNo']))
                            await send_aprs_ack(AIS,fromCall=args.botCall,toCall=packet['from'],msgNo=packet['msgNo'])
                            #discordSendTask = loop.create_task(discordChannel.send(embed=embed))
                            await discordChannel.send(embed=embed)
                            lastHeard.update({packet['from']:packet['msgNo']})
                        else:
                            logging.debug('Heard this one before - not posting, repeating ACK')
                            #ackTask = loop.create_task(send_aprs_ack(AIS,fromCall=args.botCall,toCall=packet['from'],msgNo=packet['msgNo']))
                            await send_aprs_ack(AIS,fromCall=args.botCall,toCall=packet['from'],msgNo=packet['msgNo'])
            except (aprslib.ParseError, aprslib.UnknownFormat) as exp:
                logging.info("Parsing that packet failed - unknown format.")
        
        AIS.connect()
        await loop.run_in_executor(executor=None,func=AIS.consumer(aprs_handler, raw=True, blocking=True))

    except KeyboardInterrupt:
        logging.info("Shutting down gracefully")
        #aprsMsgNo = await send_aprs_msg(AIS,fromCall=args.botCall,toCall=args.adminCall+adminSSID,message="script offline",lineNo=aprsMsgNo)
        await discordChannel.send("I'm shutting down")
        await loop.run_in_executor(executor=None,func=AIS.close())
        print('aprsMsgNo on exit: '+str(aprsMsgNo))


    #send APRS notification
    #aprsMsgNo = await send_aprs_msg(AIS,fromCall=args.botCall,toCall=args.adminCall+adminSSID,message="bot online",lineNo=aprsMsgNo)

    #run the bot

if __name__ == "__main__":
    asyncio.run(main())
