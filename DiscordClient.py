import discord
import logging
import asyncio
import janus

class DiscordClient(discord.Client):
    targetChannel : discord.channel = None
    messageQueue = None
    botNick = None
    
    lastHeard = janus.Queue()
    #    "AD8IS-10": {
    #        "thread":1234567890,   #the discord thread id used to converse with this client
    #        }
    #    }

    def __init__(self, botNick, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.botNick = botNick

    async def boot(self,botSecret):
        logging.info("Discord logging in...")
        await self.login(token=botSecret)
        logging.info("Discord logged in. Connecting...")
        discord_task = asyncio.create_task(self.connect())
        logging.info("Connection running in background. Waiting for ready.")
        await self.wait_until_ready()
        await self.change_presence(status=discord.Status.online, activity=discord.Streaming(name="APRS to Discord", url="https://www.aprs-is.net/aprsisdata.aspx"))

    async def on_ready(self):
        logging.info(f'Logged on as {self.user}!')

