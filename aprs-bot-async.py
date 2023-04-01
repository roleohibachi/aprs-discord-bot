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

#notes on structure:
#aprslib will callback aprs_callback() each time it receives a packet.
#TODO: DEBUG: aprs_callback() should send a message in discord when that happens.
#TODO: instead of just sending a message, start a thread. 
#TODO: Then, replies in the thread should be transmitted via APRS.

async def send_aprs_msg(AIS:  aprslib.IS, fromCall: str, toCall: str, message: str, lineNo: int): 
    message=re.sub(r'[{:]','',message) #sanitize

    #build a packet according to APRS spec
    pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":"+message+"{"+str(lineNo)

    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.info("Simulated send: "+pkt)
    else:
        sent=AIS.sendall(pkt)
        logging.info("Sent: "+pkt)

    #use ret value to update the serialized message count
    #(serialized msgNo's are kept since the protocol implements ACKs)
    return lineNo + 1 

async def send_aprs_ack(AIS: aprslib.IS, toCall: str, msgNo: int, fromCall: str):
    #build ACK packet per APRS spec
    pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":ack"+str(msgNo)

    #all ACKs should be sent twice. it's harder for mobile radios to receive an ACK than
    #it is to transmit a message, so double-tapping the ACK is good practice. Even so,
    #we may wind up receiving retransmitted messages that we've ACKed before. That's OK.
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
    return None #there's nothing to return for an ACK

class DiscordClient(discord.Client):
    async def on_ready(self):
        logging.info(f'Logged on as {self.user}!')

    async def on_message(self, message):
        logging.info(f'Message from {message.author}: {message.content}')
        #don't talk to yourself, silly bot
        if message.author == self.user:
            return
        #TODO debug remove this test trigger
        if message.content.startswith('$hello'):
            await message.channel.send('Hello!')

#This overloaded dict will only keep the most recent ten items, and automatically pop the oldest one.
#relies on python3 ordered dicts
from collections import Mapping
class TenRingDict(dict):
    def __init__(self, other=None, **kwargs):
        super().__init__()
        self.update(other, **kwargs)

    def __setitem__(self, key, value):
        if len(self)>=10: #gotta make room for the new value
            self.pop(next(iter(self))) #pop the oldest
        super().__setitem__(key, value)

    def update(self, other=None, **kwargs):
        if other is not None:
            for k, v in other.items() if isinstance(other, Mapping) else other:
                self[k] = v
        for k, v in kwargs.items():
            self[k] = v

async def main():
    parser = argparse.ArgumentParser(description='Bridge between APRS and Discord.')
    parser.add_argument( '-log',
        '--loglevel',
        default='warning',
        help='Use DEBUG to disable APRS transmissions. Use INFO to get lots of logging')
    
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

    #Use DEBUG to disable APRS transmissions. Use INFO to get lots of logging
    logging.basicConfig( level=args.loglevel.upper() )

    aprsMsgNo=args.aprsMsgNo

    # a record of the ten last heard APRS msgNo's.
    #This is how we keep from re-posting retransmissions to discord.
    #It's necessary because clients often don't receive their ACKs (such is radio)
    lastHeard = TenRingDict() 

    #configure Discord
    intents = discord.Intents.default()
    intents.message_content = True
    myDiscordClient = DiscordClient(intents=intents)

    #configure APRS
    AIS = aprslib.IS(args.adminCall,args.adminPass,host=args.aprsHost,port=args.aprsPort)
    AIS.set_filter("g/"+args.botCall)

    try:
        logging.info("Discord logging in...")
        await myDiscordClient.login(token=args.botSecret)
        logging.info("Discord logged in. Connecting...")
        discord_task = asyncio.create_task(myDiscordClient.connect())
        logging.info("Connection running in background. Waiting for ready.")
        await myDiscordClient.wait_until_ready()
        logging.info("Discord ready.. fetching channel.")
        discordChannel = myDiscordClient.get_channel(int(args.botChannelID))
        logging.info("Discord will use channel "+str(discordChannel))

        #await discordChannel.send("I'm online")
        await myDiscordClient.change_presence(status=discord.Status.online, activity=None)

        #packet parsing logic
        #This is going to call discordChannel.send() so I think it has to be declared 
        #here, after discordChannel.
        def aprs_callback(packet):
            try:
                packet = aprslib.parse(packet) #this requires consumer(raw=True), but allows me to handle the error myself.

                #warning: always check whether something is in the packet before trying to read it
                #or else you'll get a dict KeyError
                if 'format' in packet and packet['format'] == "message":
                    if 'response' in packet and packet['response'] == "ack":
                        logging.info("Got an ACK for message "+packet['msgNo'])
                    elif 'message_text' in packet:
                        logging.info("Got a message! Here it is: "+packet['from'] + ": " + packet['message_text']+"... msgno "+packet['msgNo'])
                        if not packet['from'] in lastHeard:
                            #this is a new client! add them to the tracker
                            #use msgNo zero to initialize - aprs spec is always a positive number
                            lastHeard.update({packet['from']:'0'})

                        if int(packet['msgNo']) > int(lastHeard[packet['from']]):
                            #note: this will run if it's a higher msgNo OR ...
                            #if msgNo was set to zero by initialization

                            #build a discord message.
                            embed={
                                        "title": packet['from']+": ",
                                        "type": "rich",
                                        "description": packet['message_text'],
                                        "url": "https://aprs.fi/?c=raw&call="+packet['from'],
                                        "timestamp": str(datetime.now()),
                                        "footer": {
                                            "text": "Licensed radio amateurs can post to this channel by sending APRS messages to callsign "+args.botCall+" with standard message format.",
                                        },
                                        "fields": [
                                            {"name": "via", "value": packet['via'], "inline": True},
                                            {"name": "msgNo", "value": packet['msgNo'], "inline": True},
                                        ],
                                    }

                            logging.info("This one's worth posting to Discord. Let's do it.")
                            asyncio.create_task(send_aprs_ack(AIS,fromCall=args.botCall,toCall=packet['from'],msgNo=packet['msgNo']))
                            asyncio.create_task(discordChannel.send(embed=discord.Embed.from_dict(embed)))
                            lastHeard.update({packet['from']:packet['msgNo']})
                        else:
                            logging.info('Heard this one before - not posting, repeating ACK')
                            asyncio.create_task(send_aprs_ack(AIS,fromCall=args.botCall,toCall=packet['from'],msgNo=packet['msgNo']))
            except (aprslib.ParseError, aprslib.UnknownFormat) as exp:
                logging.info("Parsing that packet failed - unknown format.")
        
        #this doesn't block; OK to run synchronously
        AIS.connect()

        #AIS.consumer() is a blocking synchronous function, so needs to be run in its own thread.
        #TODO: DEBUG: 
        #After ~20-30 seconds, discord complains that it hasn't been able to send heartbeats
        #and can't post messages.
        #I've tried making a coroutine that calls AIS.consumer() and loop.create_task()'ing it
        #I've tried asyncio.run_in_executor()
        #I've tried asyncio.to_thread (requires python 3.9+).
        #They all seem to not *actually* run consumer() in the background! 
        blocking_coro = asyncio.to_thread(AIS.consumer(aprs_callback, raw=True, blocking=True))
        callback_task = asyncio.create_task(blocking_coro)
        #asyncio.run_forever()

    except KeyboardInterrupt:
        logging.info("Shutting down gracefully")
        await myDiscordClient.change_presence(status=discord.Status.offline, activity=None)
        await myDiscordClient.close()

        #aprs offline notification
        #aprsMsgNo = await send_aprs_msg(AIS,fromCall=args.botCall,toCall=args.adminCall+adminSSID,message="script offline",lineNo=aprsMsgNo)

        #todo make this work
        #await loop.run_in_executor(executor=None,func=AIS.close())

        print('aprsMsgNo on exit: '+str(aprsMsgNo))


    #send APRS notification
    #aprsMsgNo = await send_aprs_msg(AIS,fromCall=args.botCall,toCall=args.adminCall+adminSSID,message="bot online",lineNo=aprsMsgNo)


if __name__ == "__main__":
    asyncio.run(main())
