import os
import re
import time
import sys
import argparse
import traceback
import logging
import aprslib
import discord
from discord.ext import commands
import asyncio

def send_aprs_msg(AIS:  aprslib.IS, fromCall: str, toCall: str, message: str, lineNo: int): 
    message=re.sub(r'[{:]','',message)
    pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":"+message.replace('{', '')+"{"+str(lineNo)
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.info("Simulated send: "+pkt)
    else:
        sent=AIS.sendall(pkt)
        logging.info("Sent: "+pkt)
    return lineNo + 1

def send_aprs_ack(AIS: aprslib.IS, toCall: str, msgNo: int, fromCall: str):
    pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":ack"+str(msgNo)
    if logging.getLogger().isEnabledFor(logging.DEBUG):
        logging.info("[DEBUG] Simulated ACK: "+pkt)
    else:
        sent=AIS.sendall(pkt)
        logging.info("Send: "+pkt)

class DiscordClient(discord.Client):
    async def on_ready(self):
        print(f'Logged on as {self.user}!')

    async def on_message(self, message):
        print(f'Message from {message.author}: {message.content}')
        if message.author == client.user:
            return
        if message.content.startswith('$hello'):
            await message.channel.send('Hello!')

if __name__ == "__main__":
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

    lastHeard = dict()

    #configure Discord
    intents = discord.Intents.default()
    intents.message_content = True
    client = DiscordClient(intents=intents)

    #configure APRS
    def aprs_handler(packet):
        try:
            logging.debug("Received APRS: "+packet)
            packet = aprslib.parse(packet)
            if 'format' in packet and packet['format'] == "message":
                if 'response' in packet and packet['response'] == "ack":
                    logging.debug("Got an ACK for message "+packet['msgNo'])
                elif 'message_text' in packet:
                    logging.debug("Got a message! Here it is: "+packet['from'] + ": " + packet['message_text']+"... msgno "+packet['msgNo'])
                    if packet['msgNo'] > lastHeard[packet['from']]:
                        discord_send(args.botChannelID,packet['from'] + ": " + packet['message_text'])
                        logging.debug("sending ACK.")
                        send_aprs_ack(AIS,fromCall=args.botCall,toCall=packet['from'],msgNo=packet['msgNo'])
                        logging.debug("Recording last heard msg number, so we don't retransmit to discord")
                        lastHeard.update({packet['from']:packet['msgNo']})
                    else:
                        logging.debug('Heard this one before - not posting')
        except (aprslib.ParseError, aprslib.UnknownFormat) as exp:
            logging.info("Parsing that packet failed - unknown format.")

    AIS = aprslib.IS(args.adminCall,args.adminPass,host=args.aprsHost,port=args.aprsPort)
    AIS.set_filter("g/"+args.botCall)
    AIS.connect()
    AIS.consumer(aprs_handler, raw=True, blocking=False)

    try:
        loop=asyncio.get_event_loop()
        discordTask = loop.create_task(client.start(args.botSecret))
        loop.run_until_complete(client.wait_until_ready())
        discordChannel = client.get_channel(int(args.botChannelID))
        loop.run_until_complete(discordChannel.send("I'm online"))
        loop.run_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down gracefully")
        #aprsMsgNo = await send_aprs_msg(AIS,fromCall=args.botCall,toCall=args.adminCall+adminSSID,message="script offline",lineNo=aprsMsgNo)
        #loop.run_until_complete(discord_send(args.botChannelID,"I'm shutting down"))
        loop.run_until_complete(discordChannel.send("I'm shutting down"))
        loop.run_until_complete(client.close())
        loop.stop()
        AIS.close()
        print('aprsMsgNo on exit: '+str(aprsMsgNo))


    #send APRS notification
    #aprsMsgNo = await send_aprs_msg(AIS,fromCall=args.botCall,toCall=args.adminCall+adminSSID,message="bot online",lineNo=aprsMsgNo)

    #run the bot
    #asyncio.run(main())

