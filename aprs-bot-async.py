import os
import time
from datetime import datetime
import argparse
import logging
import discord
import asyncio
import janus
import aprslib

from APRSClient import APRSClient
from DiscordClient import DiscordClient

#notes on structure:
#aprslib will callback aprs_callback() each time it receives a packet.
#TODO: DEBUG: aprs_callback() should send a message in discord when that happens.
#TODO: instead of just sending a message, start a thread. 
#TODO: Then, replies in the thread should be transmitted via APRS.

async def bridgeFromDiscordtoAPRS(DiscordClient, APRSClient):
    def check(message):
        #don't talk to yourself, silly bot
        if message.author == DiscordClient.user:
            return False

        logging.info(f'Message from {message.author} aka {message.author.nick}: {message.content}')

        if message.reference:
            logging.info(f'(this is a reply to item '+str(message.reference))
            #not doing anything with replies for now
            return False

        if message.channel and any(callsign['thread']==message.channel.id for callsign in DiscordClient.lastHeard.values()): 
            #this must be an APRS client thread!
            for callsign in DiscordClient.lastHeard:
                if DiscordClient.lastHeard[callsign]["thread"]==message.channel.id:
                    logging.info('(which I recognize as a reply to '+callsign)

                    #only allow club members to use this feature
                    #it is implied that club members are certified. todo verify?
                    memberRole = discord.utils.find(lambda r: r.name == "PPRAA Member", message.guild.roles) #todo make this generic
                    if memberRole in message.author.roles:
                        return True

    message = await DiscordClient.wait_for('message', check=check)
    fromCall=str(message.author.nick.split('|')[1].strip().upper().replace('Ø','0').encode('ascii', 'ignore'))
    logging.info(f"(and since you're licensed, I'll forward via aprs, {fromCall}: {message.content}")
    await message.add_reaction('\N{Clock Face One-Thirty}')
    if (await APRSClient.send_aprs_msg(toCall = APRSClient.botCall, message = fromCall+":"+str(message.content))):
        #todo make this an explicit reply, so users don't need training
        await message.clear_reaction('\N{Clock Face One-Thirty}')
        await message.add_reaction('\N{THUMBS UP SIGN}')
    else:
        await message.clear_reaction('\N{Clock Face One-Thirty}')
        await message.add_reaction('\N{Warning Sign}')


async def bridgeFromAPRStoDiscord(APRSClient, DiscordClient, packetQueue: janus.AsyncQueue):
    while True:
        packet = await packetQueue.get()
        print("found a packet on the queue: "+str(packet))
        try:
            packet = aprslib.parse(packet) #this requires consumer(raw=True), but allows me to handle the error myself.

            #warning: always check whether something is in the packet before trying to read it
            #or else you'll get a dict KeyError
            if 'format' in packet and packet['format'] == "message":
                if 'response' in packet and packet['response'] == "ack":
                    logging.info("Got an ACK for message "+packet['msgNo'])
                    APRSClient.lastHeard[packet['from']]['acks'].add(int(packet['msgNo']))
                    #todo post ACK to discord in thread
                elif 'message_text' in packet:
                    
                    logging.info("Got a message! Here it is: "+packet['from'] + ": " + packet['message_text']+"... msgno "+packet['msgNo'])

                    if not packet['from'] in APRSClient.lastHeard:
                        #this is a new client! add them to the tracker
                        #use msgNo zero to initialize - aprs spec is always a positive number
                        APRSClient.lastHeard.update({
                            packet['from']:{
                                "msgNo":0,
                                "acks":{}
                                }
                           })

                    if int(packet['msgNo']) > int(APRSClient.lastHeard[packet['from']]["msgNo"]):
                        #note: this will run if it's a higher msgNo OR ...
                        #if msgNo was set to zero by initialization

                        #build a discord message.
                        #todo thread instead of message
                        embed={
                                    "title": packet['from']+": ",
                                    "type": "rich",
                                    "description": packet['message_text'],
                                    "url": "https://aprs.fi/?c=raw&call="+packet['from'],
                                    "timestamp": str(datetime.now()),
                                    "footer": {
                                        "text": "Licensed radio amateurs can post to this channel by sending APRS messages to callsign "+APRSClient.botCall+" with standard message format.",
                                    },
                                    "fields": [
                                        {"name": "via", "value": packet['via'], "inline": True},
                                        {"name": "msgNo", "value": packet['msgNo'], "inline": True},
                                    ],
                                }

                        logging.info("This one's worth posting to Discord. Let's do it.")
                        asyncio.create_task(APRSClient.send_aprs_ack(toCall=packet['from'],msgNo=packet['msgNo']))
                        asyncio.create_task(DiscordClient.targetChannel.send(embed=discord.Embed.from_dict(embed)))
                        APRSClient.lastHeard[packet['from']].update({"msgNo":packet['msgNo']})
                    else:
                        logging.info('Heard this one before - not posting, repeating ACK')
                        asyncio.create_task(APRSClient.send_aprs_ack(toCall=packet['from'],msgNo=packet['msgNo']))
        except (aprslib.ParseError, aprslib.UnknownFormat) as exp:
            logging.info("Parsing that packet failed - unknown format.")
        packetQueue.task_done()
        print("now there are "+str(packetQueue.qsize()))

async def main():
    
    parser = argparse.ArgumentParser(description='bridgeFromAPRStoDiscord between APRS and Discord.')
    parser.add_argument( '-log',
        '--loglevel',
        default='warning',
        help='Use DEBUG to disable APRS transmissions. Use INFO to get lots of logging')
    
    parser.add_argument( '-bot','--botNick', default="aprsbot", help='Username for the bot to use in Discord')
    parser.add_argument( '--botSecret', default=os.environ.get('DISCORD_BOT_SECRET'), help='Discord bot secret.')
    parser.add_argument( '--botCall', default=os.environ.get('DISCORD_BOT_CALL'), help='Callsign for the bot to use on APRS')
    #todo parser.add_argument( '--botSSID', default=os.environ.get('DISCORD_BOT_SSID'), help='SSID for the bot to use on APRS. Useful for multiple discord channels. Include the leading dash.')
    parser.add_argument( '--botChannelID', type=int, default=int(os.environ.get('DISCORD_BOT_CHANNEL')), help='Discord channel ID to bridgeFromAPRStoDiscord.')
    parser.add_argument( '--adminCall', default=os.environ.get('APRS_CALL'), help='Callsign to authenticate with APRS. Under whose license are you transmitting?')
    parser.add_argument( '--adminPass', default=os.environ.get('APRS_PASSWD'), help='Password for the APRS user.')
    parser.add_argument( '--aprsHost', default="noam.aprs2.net", help='APRS-IS server')
    parser.add_argument( '--aprsPort', type=int, default=14580, help='APRS-IS port')
    parser.add_argument( '--aprsMsgNo', type=int, default=int(time.time()/10%(pow(10,2))), help='The initial serialized message number. If unset, will be random.')
    args = parser.parse_args()

    #Use DEBUG to disable APRS transmissions. Use INFO to get lots of logging
    logging.basicConfig( level=args.loglevel.upper() )
    #if logging.getLogger().isEnabledFor(logging.DEBUG): loop.set_debug(True)

    if not args.adminPass:
        args.adminPass = aprslib.passcode(args.adminCall)
        logging.warn("You should provide a passcode. I'm guessing it should be " + args.adminPass)

    #configure APRS
    myPacketQueue = janus.Queue() #thread-safe queue for aprs packets received
    myAPRSClient = APRSClient(myPacketQueue.sync_q, args.botCall, args.aprsMsgNo)
    myAPRSClient.AIS = aprslib.IS(args.adminCall,args.adminPass,host=args.aprsHost,port=args.aprsPort)
    myAPRSClient.AIS.set_filter("g/"+args.botCall)

    #configure Discord
    intents = discord.Intents.default()
    intents.message_content = True
    myDiscordClient = DiscordClient(args.botNick, intents=intents)

    try:

        #start discord
        await myDiscordClient.boot(args.botSecret)
        logging.info("Discord ready.. fetching channel.")
        myDiscordClient.targetChannel = myDiscordClient.get_channel(int(args.botChannelID))
        logging.info("Discord will use channel "+str(myDiscordClient.targetChannel))

        #start APRS
        myAPRSClient.AIS.connect()

        #AIS.consumer() is a blocking synchronous function, so needs to be run in its own thread.
        #(which is why we need janus, a thread-safe queue)
        asyncio.create_task(bridgeFromAPRStoDiscord(myAPRSClient, myDiscordClient, myPacketQueue.async_q))
        await myAPRSClient.makeThreadedConsumer(asyncio.get_event_loop())

        #execution should never reach this point
        raise asyncio.CancelledError

    except asyncio.CancelledError:

        #shutdown discord
        logging.info('setting status offline')
        await myDiscordClient.change_presence(status=discord.Status.offline, activity=None)
        logging.info('closing discord')
        await myDiscordClient.close()
        logging.info('discord is closed.')

        #shutdown aprs
        logging.info('closing aprs')
        myAPRSClient.AIS.close()
        logging.info('aprs is closed')
        
        myPacketQueue.close()
        await myPacketQueue.wait_closed()

        return str(myAPRSClient.aprsMsgNo)


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
