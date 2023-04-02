import os
import sys
import re
import time
from datetime import datetime
import argparse
import traceback
import logging
import aprslib
import discord
from discord.ext import commands
import asyncio
import janus
import functools

#notes on structure:
#aprslib will callback aprs_callback() each time it receives a packet.
#TODO: DEBUG: aprs_callback() should send a message in discord when that happens.
#TODO: instead of just sending a message, start a thread. 
#TODO: Then, replies in the thread should be transmitted via APRS.

class APRSClient:
    AIS = None
    packetQueue = None

    def __init__(self, packetQueue):
        self.packetQueue = packetQueue

    #This overloaded dict will only keep the most recent ten items, 
    #and automatically pop the oldest one.
    #relies on python3 ordered dicts
    #It's necessary because clients often don't receive their ACKs (such is radio)
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
    
    lastHeard = TenRingDict() 
    # format: {
    #    "AD8IS-10": {
    #        "msgNo":10,
    #        "thread":1234567890,
    #        "acks":{34,35,36} #this is a set, so won't have dupes
    #        }
    #    }

    async def _checkRx(msgNo,lastHeard):
        while True:
            asyncio.sleep(1)
            if any(msgNo in callsign["acks"] for callsign in lastHeard.values()):
                return True
    
    async def send_aprs_msg(AIS:  aprslib.IS, fromCall: str, toCall: str, message: str, lineNo: int): 
        message=re.sub(r'[{:]','',message).encode('ascii','ignore')[:67] #sanitize
        tries=3
        counter=0
    
        #build a packet according to APRS spec
        pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":"+message+"{"+str(lineNo)
        
        while counter < tries:
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                logging.info("Simulated send: "+pkt)
            else:
                sent=AIS.sendall(pkt)
                logging.info("Sent: "+pkt)
            counter+=1
            result = wait_for(_checkRx(message.number),timeout=30)
            if result:
                return True
        return False
    
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

    def aprs_callback(packet):
        packetQueue.sync_q.put(packet)
        logging.info("put a packet on the queue, there are now "+str(packetQueue.sync_q.qsize()))
        packetQueue.sync_q.join()

    def aprsFuture(loop):
        return loop.run_in_executor(None, functools.partial(AIS.consumer, aprs_callback, immortal=True, raw=True, blocking=True))

        

class DiscordClient(discord.Client):
    packetQueue = None
    def __init__(self, packetQueue, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.packetQueue = packetQueue

    async def on_ready(self):
        logging.info(f'Logged on as {self.user}!')

    async def on_message(self, message):

        #don't talk to yourself, silly bot
        if message.author == self.user:
            return

        logging.info(f'Message from {message.author} aka {message.author.nick}: {message.content}')

        if message.reference:
            logging.info(f'(this is a reply to item '+str(message.reference))
            #not doing anything with replies for now

        if message.channel and any(callsign['thread']==message.channel.id for callsign in lastHeard.values()): #this is in a thread I care about
            for callsign in lastHeard:
                if lastHeard[callsign]["thread"]==message.channel.id:
                    logging.info('(which I recognize as a reply to '+callsign)
                    memberRole = discord.utils.find(lambda r: r.name == "PPRAA Member", message.guild.roles)
                    if memberRole in message.author.roles:
                        fromCall=str(message.author.nick.split('|')[1].strip().upper().replace('Ø','0').encode('ascii', 'ignore'))
                        logging.info(f"(and since you're licensed, I'll forward via aprs, from {fromCall}")
                        await message.add_reaction('\N{Clock Face One-Thirty}')
                        if (await trySend(str(message)))
                            await message.clear_reaction('\N{Clock Face One-Thirty}')
                            await message.add_reaction('\N{THUMBS UP SIGN}')
                        else:
                            await message.clear_reaction('\N{Clock Face One-Thirty}')
                            await message.add_reaction('\N{Warning Sign}')

        #TODO debug remove this test trigger
        if message.content.startswith('$hello'):
            await message.channel.send('Hello!')

async def bridge(AIS, discordChannel, queue: janus.AsyncQueue, lastHeard, args):
    while True:
        packet = await queue.get()
        print("found a packet on the queue: "+str(packet))
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
                        lastHeard.update({
                            packet['from']:{
                                "msgNo":0,
                                "thread":None,
                                "acks":{}
                                }
                           })

                    if int(packet['msgNo']) > int(lastHeard[packet['from']]["msgNo"]):
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
                        lastHeard[packet['from']].update({"msgNo":packet['msgNo']})
                    else:
                        logging.info('Heard this one before - not posting, repeating ACK')
                        asyncio.create_task(send_aprs_ack(AIS,fromCall=args.botCall,toCall=packet['from'],msgNo=packet['msgNo']))
        except (aprslib.ParseError, aprslib.UnknownFormat) as exp:
            logging.info("Parsing that packet failed - unknown format.")
        queue.task_done()
        print("now there are "+str(queue.qsize()))

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
    #if logging.getLogger().isEnabledFor(logging.DEBUG): loop.set_debug(True)

    aprsMsgNo=args.aprsMsgNo

    #This queue is thread-safe.
    #aprslib puts packets in queue from one thread
    #discord gets those packets from the queue and processes them in another
    packetQueue = janus.Queue()

    #configure Discord
    intents = discord.Intents.default()
    intents.message_content = True
    myDiscordClient = DiscordClient(packetQueue, intents=intents)

    #configure APRS
    myAPRSClient = APRSClient(packetQueue)
    myAPRSClient.AIS = aprslib.IS(args.adminCall,args.adminPass,host=args.aprsHost,port=args.aprsPort)
    myAPRSClient.AIS.set_filter("g/"+args.botCall)

    try:
        #start discord
        logging.info("Discord logging in...")
        await myDiscordClient.login(token=args.botSecret)
        logging.info("Discord logged in. Connecting...")
        discord_task = asyncio.create_task(myDiscordClient.connect())
        logging.info("Connection running in background. Waiting for ready.")
        await myDiscordClient.wait_until_ready()
        logging.info("Discord ready.. fetching channel.")
        discordChannel = myDiscordClient.get_channel(int(args.botChannelID))
        logging.info("Discord will use channel "+str(discordChannel))

        await myDiscordClient.change_presence(status=discord.Status.online, activity=None)

        #start APRS
        myAPRSClient.AIS.connect()

        #AIS.consumer() is a blocking synchronous function, so needs to be run in its own thread.
        #(which is why we need janus, a thread-safe queue)
        await bridge(myAPRSClient.AIS, discordChannel, packetQueue.async_q,lastHeard,args)
        await myAPRSClient.aprsFuture()

        #execution should never reach this point
        raise asyncio.CancelledError

    except asyncio.CancelledError:

        #shutdown discord
        logging.info('setting status offline')
        await myDiscordClient.change_presence(status=discord.Status.offline, activity=None)
        logging.info('closing discord')
        await myDiscordClient.close()
        logging.info('discord is closed.')
        discord_task.cancel()

        #shutdown aprs
        logging.info('closing aprs')
        AIS.close()
        logging.info('aprs is closed')
        
        packetQueue.close()
        await packetQueue.wait_closed()

        return str(aprsMsgNo)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    mainTask = None
    try:
        mainTask = loop.create_task(main())
        result = loop.run_until_complete(mainTask)
        print('aprsMsgNo on exit: '+str(mainTask))
    except KeyboardInterrupt as e:
        logging.info("Shutting down gracefully")
        if mainTask:
            mainTask.cancel()
        mainTask = loop.run_until_complete(asyncio.wait_for(mainTask, timeout=5))
        logging.info('done cancelling')
        print('aprsMsgNo on exit: '+str(mainTask))
        os._exit(0) #let OS kill remaining threads
