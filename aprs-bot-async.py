import os
import re
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

async def bridgeFromDiscordtoAPRS(DiscordClient, APRSClient):
    def check(message):

        #don't talk to yourself, silly bot
        if message.author == DiscordClient.user:
            return False

        #not doing anything with replies for now
        if message.reference:
            logging.info(f'(this is a reply to item '+str(message.reference))
            #but the message content should still work, if it's in a thread

        #only listen in the authorized channel
        try:
            if message.channel.parent.id == DiscordClient.targetChannel.id:
                #this message is in a thread, in the correct channel...
                logging.info(message.author.nick+" sent a threaded message in my channel")
                if(re.search(' via APRS$',message.channel.name)):
                    fromCall = re.split(' via APRS$',message.channel.name)[0]
                    if not fromCall in DiscordClient.lastHeard:
                        logging.info("(which is a reply to an old thread of mine. Adding it to lastHeard.")
                        DiscordClient.lastHeard.update({fromCall:{"thread":message.channel.id}})
            else:
                logging.info(f'Not for me, but a threaded message from {message.author.nick} in {str(message.channel.name)}: {message.content}')
                return False
        except AttributeError:
            logging.info(f'Not for me, but a message from {message.author.nick} in {str(message.channel.name)}: {message.content}')
            return False

        #only allow club members to use this feature
        requiredRoles = ["PPRAA Members","General Hams"]
        for role in requiredRoles:
            discordRole = discord.utils.find(lambda r: r.name == role, message.guild.roles) #TODO get this from environment or something more standard
            if not discordRole in message.author.roles:
                authorizedUser = False
                logging.info("But they're not allowed to send radio messages without the role: "+role)
                return False
        logging.info('(sent by a club member who may use this service')

        if message.channel and any(callsign['thread']==message.channel.id for callsign in DiscordClient.lastHeard.values()): 
            #this must be an APRS client thread!
            for callsign in DiscordClient.lastHeard:
                if DiscordClient.lastHeard[callsign]["thread"]==message.channel.id:
                    logging.info('(which I recognize as a reply to '+callsign)
                    return True

    while True:
        message = await DiscordClient.wait_for('message', check=check)

        fromCall: str = (message.author.nick.split('|')[1].strip().upper().replace('Ø','0').encode('ascii', 'ignore')).decode('ascii')
        toCall: str = ([key for key, value in DiscordClient.lastHeard.items() if value['thread'] == message.channel.id][0])
        logging.info(f"forwarding via aprs, {fromCall} -> {toCall}: {message.content}")

        try:
            #replyMessage = await message.reply("I will try to transmit this message 3 times over the next 90 seconds. If the recipient acknowledges, then you'll see a green check mark on your message. No check mark means no acknowledgement was received; however the message might still have been delivered.", delete_after=90) 
            await message.add_reaction('\N{outbox tray}')
            await APRSClient.send_aprs_msg(toCall = toCall, message = fromCall+"-"+message.content)
            await message.add_reaction('\N{Mobile Phone with Rightwards Arrow at Left}')
            #await replyMessage.delete()
        except asyncio.exceptions.TimeoutError:
            logging.info("The message was not ACKed before the timeout.")
            await message.add_reaction('\N{White Question Mark Ornament}')
            #await replyMessage.delete()


async def bridgeFromAPRStoDiscord(APRSClient, DiscordClient, packetQueue: janus.AsyncQueue):
    while True:
        packet = await packetQueue.get()
        logging.info("found a packet on the queue: "+str(packet))
        try:
            packet = aprslib.parse(packet) #this requires consumer(raw=True), but allows me to handle the error myself.

            #warning: always check whether something is in the packet before trying to read it
            #or else you'll get a dict KeyError
            if 'format' in packet and packet['format'] == "message":
                if 'response' in packet and packet['response'] == "ack":
                    logging.info("Got an ACK for message "+packet['msgNo'])
                    if not packet['from'] in APRSClient.lastHeard:
                        APRSClient.lastHeard.update({packet['from']:{'acks':{int(packet['msgNo'])}}})
                    else:
                        if not 'acks' in APRSClient.lastHeard[packet['from']]:
                            APRSClient.lastHeard[packet['from']].update({'acks':set([int(packet['msgNo'])])})
                        else:
                            APRSClient.lastHeard[packet['from']]['acks'].add(int(packet['msgNo']))
                    
                elif 'message_text' in packet:
                    
                    logging.info("Got a message! Here it is: "+packet['from'] + ": " + packet['message_text']+"... msgno "+packet['msgNo'])

                    if not packet['from'] in APRSClient.lastHeard:
                        #this is a new client! add them to the tracker
                        #use msgNo zero to initialize - aprs spec is always a positive number
                        APRSClient.lastHeard.update({
                            packet['from']:{
                                "msgNo":0,
                                "acks":set(),
                                "nextMsgNo":1,
                                }
                           })

                    if int(packet['msgNo']) > int(APRSClient.lastHeard[packet['from']]["msgNo"]):
                        #note: this will run if it's a higher msgNo OR ...
                        #if msgNo was set to zero by initialization

                        #build a discord message.
                        embed={
                                    "title": packet['from']+": ",
                                    "type": "rich",
                                    "description": packet['message_text'],
                                    "url": "https://aprs.fi/?c=raw&call="+packet['from'],
                                    "timestamp": str(datetime.now()),
                                    "fields": [
                                        {"name": "via", "value": packet['via'], "inline": True},
                                        {"name": "msgNo", "value": packet['msgNo'], "inline": True},
                                    ],
                                }

                        logging.info("This one's worth posting to Discord. Let's do it.")
                        
                        if packet['from'] in DiscordClient.lastHeard:
                            #use known thread
                            targetThread = DiscordClient.targetChannel.get_thread(DiscordClient.lastHeard[packet['from']]["thread"])
                        else:
                            #create a new thread
                            targetThread = await DiscordClient.targetChannel.create_thread(name=packet['from']+" via APRS",message=None, slowmode_delay=30, type=discord.ChannelType.public_thread)
                            DiscordClient.lastHeard.update({packet['from']:{"msgNo":packet['msgNo'],"thread":targetThread.id}})
                            logging.info("created thread "+str(targetThread.id))
                            await targetThread.send("Licensed radio amateurs can reply in this thread. If permitted, it will be retransmitted via APRS-IS in reply to "+packet['from'])
                        
                        #send message in thread
                        asyncio.create_task(targetThread.send(embed=discord.Embed.from_dict(embed)))

                        #acknowledge delivery via APRS
                        asyncio.create_task(APRSClient.send_aprs_ack(toCall=packet['from'],msgNo=packet['msgNo']))
                        APRSClient.lastHeard[packet['from']].update({"msgNo":packet['msgNo']})

                    else:
                        logging.info('Heard this one before - not posting, repeating ACK')
                        asyncio.create_task(APRSClient.send_aprs_ack(toCall=packet['from'],msgNo=packet['msgNo']))
        except (aprslib.ParseError, aprslib.UnknownFormat) as exp:
            logging.info("Parsing that packet failed - unknown format.")
        packetQueue.task_done()
        logging.info("now there are "+str(packetQueue.qsize()))

async def main():
    
    parser = argparse.ArgumentParser(description='bridgeFromAPRStoDiscord between APRS and Discord.')
    parser.add_argument( '-log',
        '--loglevel',
        default='warning',
        help='Use DEBUG to disable APRS transmissions. Use INFO to get lots of logging')
    
    parser.add_argument( '-bot','--botNick', default="aprsbot", help='Username for the bot to use in Discord')
    parser.add_argument( '--botSecret', default=os.environ.get('DISCORD_BOT_SECRET'), help='Discord bot secret.')
    parser.add_argument( '--botCall', default=os.environ.get('DISCORD_BOT_CALL'), help='Callsign for the bot to use on APRS')
    parser.add_argument( '--botChannelID', type=int, default=int(os.environ.get('DISCORD_BOT_CHANNEL')), help='Discord channel ID to bridgeFromAPRStoDiscord.')
    parser.add_argument( '--adminCall', default=os.environ.get('APRS_CALL'), help='Callsign to authenticate with APRS. Under whose license are you transmitting?')
    parser.add_argument( '--adminPass', default=os.environ.get('APRS_PASSWD'), help='Password for the APRS user.')
    parser.add_argument( '--aprsHost', default="noam.aprs2.net", help='APRS-IS server')
    parser.add_argument( '--aprsPort', type=int, default=14580, help='APRS-IS port')
    parser.add_argument( '--aprsMsgNo', type=int, default=int(time.time()/10%(pow(10,2))), help='The initial serialized message number. If unset, will be random.')
    args = parser.parse_args()

    #Use DEBUG to disable APRS transmissions. Use INFO to get lots of logging
    logging.basicConfig( level=args.loglevel.upper(), format='%(asctime)s: %(message)s' )
    #if logging.getLogger().isEnabledFor(logging.DEBUG): loop.set_debug(True)

    if not args.adminPass:
        args.adminPass = aprslib.passcode(args.adminCall)
        logging.warn("You should provide a passcode. I'm guessing it should be " + args.adminPass)

    #configure APRS
    myPacketQueue = janus.Queue() #thread-safe queue for aprs packets received
    myAPRSClient = APRSClient(myPacketQueue.sync_q, str(args.botCall))
    myAPRSClient.AIS = aprslib.IS(args.adminCall,args.adminPass,host=args.aprsHost,port=args.aprsPort)
    myAPRSClient.AIS.set_filter("g/"+args.botCall)

    #configure Discord
    intents = discord.Intents.default()
    intents.message_content = True
    myDiscordClient = DiscordClient(args.botNick, intents=intents)

    try:

        #start discord
        await myDiscordClient.boot(args.botSecret)
        await myDiscordClient.change_presence(status=discord.Status.online, activity=discord.Activity(type=discord.ActivityType.listening, name='APRS-IS for "'+args.botCall+'"'))
        logging.info("Discord ready.. fetching channel.")
        myDiscordClient.targetChannel = myDiscordClient.get_channel(int(args.botChannelID))
        logging.info("Discord will use channel "+str(myDiscordClient.targetChannel))
        
        #This is commented out for a reason
        #You can run this to purge the bot's old messages
        #dangerous if you don't know what you're doing!
        #delete my old messages
        #todel=set()
        #async for message in myDiscordClient.targetChannel.history(limit = 100):
        #    if message.author.name == 'aprsbot':
        #        todel.add(message)
        #for message in todel:
        #    await message.delete()
        #for thread in myDiscordClient.targetChannel.threads:
        #    await thread.delete()

        #start APRS
        myAPRSClient.AIS.connect()

        #AIS.consumer() is a blocking synchronous function, so needs to be run in its own thread.
        #(which is why we need janus, a thread-safe queue)
        asyncio.create_task(bridgeFromAPRStoDiscord(myAPRSClient, myDiscordClient, myPacketQueue.async_q))
        asyncio.create_task(bridgeFromDiscordtoAPRS(myDiscordClient,myAPRSClient))
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


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    mainTask = None
    try:
        mainTask = loop.create_task(main())
        result = loop.run_until_complete(mainTask)
        #this should never finish
        raise KeyboardInterrupt
    except KeyboardInterrupt as e:
        logging.info("Shutting down gracefully")
        if mainTask:
            mainTask.cancel()
        mainTask = loop.run_until_complete(asyncio.wait_for(mainTask, timeout=5))
        logging.info('done cancelling')
        os._exit(0) #let OS kill remaining threads
