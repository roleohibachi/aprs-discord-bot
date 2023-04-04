import os
import re
import time
from datetime import datetime
import sys
import argparse
import traceback
import logging
import discord
from discord.ext import commands
import asyncio

lastHeard = {
        "AD8IS-10": {
            "msgNo":0,
            "thread":None,
            "acks":{34,35,36}
            }
        }

async def checkRx(msgNo):
    while True:
        asyncio.sleep(1)
        if any(msgNo in callsign["acks"] for callsign in lastHeard.values()):
            return True

async def trySend(message):
    counter=0
    while counter < 3:
        logger.info("I would send an aprs message and await an ack")
        counter+=1
        result = wait_for(checkRx(message.number),timeout=30)
        if result:
            return True
    return False

class DiscordClient(discord.Client):
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

        if message.channel and any(callsign['thread']==message.channel.id for callsign in lastHeard.values()):
            for callsign in lastHeard:
                if lastHeard[callsign]["thread"]==message.channel.id:
                    logging.info('(which I recognize as a reply to '+callsign)
                    memberRole = discord.utils.find(lambda r: r.name == "PPRAA Member", message.guild.roles)
                    if memberRole in message.author.roles:
                        fromCall=str(message.author.nick.split('|')[1].strip().upper().replace('\N{Latin Capital Letter O with Stroke}','0').encode('ascii', 'ignore'))
                        logging.info(f"(and since you're licensed, I'll forward via aprs, from {fromCall}")
                        await message.add_reaction('\N{Clock Face One-Thirty}')
                        if (await trySend(fromCall+": "+str(message.content.encode('ascii','ignore'))))
                            await message.clear_reaction('\N{Clock Face One-Thirty}')
                            await message.add_reaction('\N{THUMBS UP SIGN}')
                        else:
                            await message.clear_reaction('\N{Clock Face One-Thirty}')
                            await message.add_reaction('\N{Warning Sign}')


        #TODO debug remove this test trigger
        if message.content.startswith('$hello'):
            await message.channel.send('Hello!')

async def main(loop):
    
    parser = argparse.ArgumentParser(description='Bridge between APRS and Discord.')
    parser.add_argument( '-log',
        '--loglevel',
        default='warning',
        help='Use DEBUG to disable APRS transmissions. Use INFO to get lots of logging')
    
    parser.add_argument( '-bot','--botName', default="aprsbot", help='Username for the bot to use in Discord')
    parser.add_argument( '--botSecret', default=os.environ.get('DISCORD_BOT_SECRET'), help='Discord bot secret.')
    parser.add_argument( '--botChannelID', type=int, default=int(os.environ.get('DISCORD_BOT_CHANNEL')), help='Discord channel ID to bridge.')
    args = parser.parse_args()

    #Use DEBUG to disable APRS transmissions. Use INFO to get lots of logging
    logging.basicConfig( level=args.loglevel.upper() )
    #if logging.getLogger().isEnabledFor(logging.DEBUG): loop.set_debug(True)

    #configure Discord
    intents = discord.Intents.default()
    intents.message_content = True
    myDiscordClient = DiscordClient(intents=intents)

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

        #message received via aprs!
        fromCall="AD8IS-10"
        msgNo=69
        contents="sup dog"
        firstThread = await discordChannel.create_thread(name=fromCall+" via APRS",message=None, type=discord.ChannelType.public_thread) #todo slowmode
        lastHeard.update({fromCall:{"msgNo":msgNo,"thread":firstThread.id}})
        logging.info("created thread "+str(firstThread.id))
        await firstThread.send("Licensed radio amateurs can reply in this thread. If a valid callsign is in the username, it will be retransmitted via APRS-IS in reply to "+fromCall)
        await asyncio.sleep(5)

        embed={
                "title": fromCall+":",
                "type": "rich",
                "description": contents,
                "url": "https://aprs.fi/?c=raw&call="+fromCall,
                "timestamp": str(datetime.now()),
                "fields": [
                    {"name": "via", "value": "N0CALL", "inline": True},
                    {"name": "msgNo", "value": "69", "inline": True},
                ],
            }
        secondMessage = await firstThread.send(embed=discord.Embed.from_dict(embed))
        await asyncio.sleep(50)

        logging.info(str(firstThread.id))
        raise asyncio.CancelledError("my job here is done")

    except asyncio.CancelledError:

        #shutdown discord
        logging.info('setting status offline')
        await myDiscordClient.change_presence(status=discord.Status.offline, activity=None)
        logging.info('closing discord')
        await myDiscordClient.close()
        logging.info('discord is closed.')
        discord_task.cancel()

        return 


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    mainTask = None
    try:
        mainTask = loop.create_task(main(loop))
        result = loop.run_until_complete(mainTask)
        loop.run_forever()
    except KeyboardInterrupt as e:
        logging.info("Shutting down gracefully")
        if mainTask:
            mainTask.cancel()
        mainTask = loop.run_until_complete(asyncio.wait_for(mainTask, timeout=5))
        logging.info('done cancelling')
        os._exit(0) #let OS kill remaining threads
