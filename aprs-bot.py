import aprslib
import os
import re
import time
import sys
import traceback
from discordwebhook import Discord

live=True

def send_aprs_msg(AIS:  aprslib.IS, fromCall: str, toCall: str, message: str, lineNo: int): 
    global live
    message=re.sub(r'[{:]','',message)
    pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":"+message.replace('{', '')+"{"+str(lineNo)
    if(live):
        sent=AIS.sendall(pkt)
        print("Send: "+pkt)
    else:
        print("[DEBUG] Simulated send: "+pkt)
    return lineNo + 1

def send_aprs_ack(AIS: aprslib.IS, toCall: str, msgNo: int, fromCall: str):
    global live
    pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":ack"+str(msgNo)
    if(live):
        sent=AIS.sendall(pkt)
        print("Send: "+pkt)
    else:
        print("[DEBUG] Simulated ACK: "+pkt)


def main():

    #configure APRS
    botCall="PPRAA"
    adminCall="AD8IS"
    adminSSID="-10"
    passwd=os.environ['APRS_PASSWD']
    
    msgcount=int(time.time()%(pow(10,4))) #gotta start somewhere
    AIS = aprslib.IS(adminCall,passwd,host="noam.aprs2.net",port=14580)
    #AIS.set_login(adminCall,passwd)
    #AIS.set_server("noam.aprs2.net",port=14580)
    AIS.set_filter("g/"+botCall)

    #configure Discord
    discord = Discord(url=os.environ['DISCORD_WEBHOOK_URL'])

    def aprs_handler(packet):
        print(packet)
        if packet['format'] == "message":
            if 'response' in packet:
                if packet['response'] == "ack":
                    print("Got an ACK for message "+packet['msgNo'])
            elif 'message_text' in packet:
                print("Got a message! Here it is: "+packet['from'] + ": " + packet['message_text']+"... msgno "+packet['msgNo'])
                discord.post(
                    embeds=[
                        {
                            "author": {
                                "name": "New APRS Message Received",
                                "url": "https://aprs.fi/?c=raw&call="+packet['from'],
                            },
                            "title": packet['from'],
                            "description": packet['message_text'],
                            "fields": [
                                {"name": "via", "value": packet['via'], "inline": True},
                                {"name": "msgNo", "value": packet['msgNo'], "inline": True},
                            ],
                            "footer": {
                                "text": "Licensed radio amateurs can reply to this message using APRS. Replies in this channel will not be forwarded.",
                            },
                        }
                    ],
                )


                print("sending ACK.")
                send_aprs_ack(AIS,fromCall=botCall,toCall=packet['from'],msgNo=packet['msgNo'])

    try:

        AIS.connect()
        msgcount = send_aprs_msg(AIS,fromCall=botCall,toCall=adminCall+adminSSID,message="script online",lineNo=msgcount)
        discord.post(content="bot online")

        # by default `raw` is False, then each line is ran through aprslib.parse()
        AIS.consumer(aprs_handler, raw=False)

        #we should never make it to here
        AIS.close()
        discord.post(content="bot offline")

    except KeyboardInterrupt:
        print("Shutdown requested... notifying admin")
        msgcount = send_aprs_msg(AIS,fromCall=botCall,toCall=adminCall+adminSSID,message="script offline",lineNo=msgcount)
        AIS.close()
        discord.post(content="bot offline")
    except Exception as err:
        AIS.close()
        discord.post(content="bot offline")
        print(traceback.format_exc())
        sys.exit(1)
    
    sys.exit(0)

if __name__ == "__main__":
    main()
