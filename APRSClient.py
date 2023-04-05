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
    #        "nextMsgNo":1          #the next message number to use with this client
    #        }
    #    }

    def __init__(self, packetQueue, botCall):
        self.botCall = botCall
        self.packetQueue = packetQueue

    async def _checkRx(self, toCall, msgNo) -> bool:
        #await this with a timeout
        #it will poll on lastHeard to see if a client has ACKed a msgNo.
        while True:
            await asyncio.sleep(1)
            if msgNo in self.lastHeard[toCall]["acks"]:
                return True

    def sanitize_aprs_msg(self, message) -> str:
        return re.sub(r'[{:]','',message).encode('ascii','ignore').decode('ascii')[:67] #sanitize
    
    async def send_aprs_msg(self, toCall: str, message: str, fromCall: str = None) -> bool: 

        if not fromCall:
            fromCall = self.botCall

        if toCall in self.lastHeard and "nextMsgNo" in self.lastHeard[toCall]:
            msgNo = self.lastHeard[toCall]["nextMsgNo"]
        else:
            msgNo = 1

        if not self.lastHeard[toCall]:
            self.lastHeard.update({toCall:{"nextMsgNo":msgNo+1}})
        else:
            self.lastHeard[toCall].update({"nextMsgNo":msgNo+1})

        message=self.sanitize_aprs_msg(message)

        tries=3
        counter=0
    
        #build a packet according to APRS spec
        pkt=str(fromCall)+">APP614"+",TCPIP*::"+str(toCall.ljust(9, " "))+":"+str(message)+"{"+str(msgNo)
        
        for _ in range(tries):
            if logging.getLogger().isEnabledFor(logging.DEBUG):
                logging.info("Simulated send: "+pkt, extra={'className': self.__class__.__name__})
            else:
                sent=self.AIS.sendall(pkt)
                logging.info("Sent: "+pkt, extra={'className': self.__class__.__name__})
            result = await asyncio.wait_for(self._checkRx(toCall, msgNo),timeout=30)
            if result:
                logging.info("the message was acknowledged within the timeout period.")
                return True
        logging.info("Timeout reached; no ACK received for message "+str(msgNo))
        return False
    
    async def send_aprs_ack(self, toCall: str, msgNo: int, fromCall: str = None):
        if not fromCall:
            fromCall = self.botCall

        #build ACK packet per APRS spec
        pkt=fromCall+">APP614"+",TCPIP*::"+toCall.ljust(9, " ")+":ack"+str(msgNo)
    
        #all ACKs should be sent twice. it's harder for mobile radios to receive an ACK than
        #it is to transmit a message, so double-tapping the ACK is good practice. Even so,
        #we may wind up receiving retransmitted messages that we've ACKed before. That's OK.
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            logging.info("[DEBUG] Simulated ACK: "+pkt, extra={'className': self.__class__.__name__})
            await asyncio.sleep(30)
            logging.info("[DEBUG] Simulated ACK (double-tap): "+pkt, extra={'className': self.__class__.__name__})
        else:
            logging.info("Sending ACK: "+pkt, extra={'className': self.__class__.__name__})
            sent=self.AIS.sendall(pkt)
            await asyncio.sleep(30)
            logging.info("Sending ACK (double-tap): "+pkt, extra={'className': self.__class__.__name__})
            sent=self.AIS.sendall(pkt)
        return None #there's nothing to return for an ACK

    def aprs_callback(self, packet):
        self.packetQueue.put(packet)
        logging.info("put a packet on the queue, there are now "+str(self.packetQueue.qsize()), extra={'className': self.__class__.__name__})

    def makeThreadedConsumer(self, loop):
        return loop.run_in_executor(None, functools.partial(self.AIS.consumer, self.aprs_callback, immortal=True, raw=True, blocking=True))

        
