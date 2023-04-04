import functools
import aprslib
import asyncio
import re 
import logging

from RingDict import RingDict 

class APRSClient:
    lastHeard = RingDict(size=10)
    #    "AD8IS-10": {
    #        "msgNo":10,            #the last msgNo received from this client (which we ACKed)
    #        "acks":{34,35,36}      #a set (no dupes) of acks received from this client (for messages we sent)
    #        }
    #    }

    def __init__(self, packetQueue, botCall, initialMsgNo):
        self.aprsMsgNo = initialMsgNo
        self.botCall = botCall
        self.packetQueue = packetQueue

    async def _checkRx(self, msgNo) -> bool:
        #await this with a timeout
        #it will poll on lastHeard to see if a client has ACKed a msgNo.
        while True:
            asyncio.sleep(1)
            if any(msgNo in callsign["acks"] for callsign in self.lastHeard.values()):
                return True

    def sanitize_aprs_msg(message):
        return re.sub(r'[{:]','',message).encode('ascii','ignore')[:67] #sanitize
    
    async def send_aprs_msg(self, toCall: str, message: str, fromCall: str = None, msgNo: int = None): 
        if not fromCall:
            fromCall = self.botCall
        if not msgNo:
            msgNo = self.aprsMsgNo

        message=self.sanitize_aprs_msg(message)

        tries=3
        counter=0
    
        #build a packet according to APRS spec
        pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":"+message+"{"+str(msgNo)
        
        while counter < tries:
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                logging.info("Simulated send: "+pkt)
            else:
                sent=self.AIS.sendall(pkt)
                logging.info("Sent: "+pkt)
            counter+=1
            result = asyncio.wait_for(self._checkRx(message.number),timeout=30)
            if result:
                return True
        return False
    
        #use ret value to update the serialized message count
        #(serialized msgNo's are kept since the protocol implements ACKs)
        return msgNo + 1 
    
    async def send_aprs_ack(self, toCall: str, msgNo: int, fromCall: str = None):
        if not fromCall:
            fromCall = self.botCall

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
            sent=self.AIS.sendall(pkt)
            await asyncio.sleep(30)
            logging.info("Sending ACK (double-tap): "+pkt)
            sent=self.AIS.sendall(pkt)
        return None #there's nothing to return for an ACK

    def aprs_callback(self, packet):
        self.packetQueue.put(packet)
        logging.info("put a packet on the queue, there are now "+str(self.packetQueue.qsize()))

    def makeThreadedConsumer(self, loop):
        return loop.run_in_executor(None, functools.partial(self.AIS.consumer, self.aprs_callback, immortal=True, raw=True, blocking=True))

        
