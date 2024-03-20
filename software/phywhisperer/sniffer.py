# Adapted from https://github.com/usb-tools/pyopenvizsla
#
# Copyright 2019 OpenVizsla.org
# 
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 
# 1. Redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer.
# 
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
# 
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Core USB packet sniffer packet backend for PhyWhisperer, adapted from pyopenvizsla.
"""

import crcmod
from .protocol import PWPacketHandler

def hd(x):
    return " ".join("%02x" % i for i in x)


class USBEventSink:
    """ Base class for USB event "sinks", which listen for USB events. """

    def handle_usb_packet(self, timestamp, buffer, flags):
        """ Core functionality for a USB event sink -- reports each USB packet as it comes in. 
        
        Args:
            timestamp -- The timestamp for the given packet.
        """
        pass


class USBSniffer(PWPacketHandler):
    """ USB Sniffer packet sink -- receives sniffed packets from the PhyWhisperer. """

    data_crc = staticmethod(crcmod.mkCrcFun(0x18005))

    def __init__(self):
        """ Set up our core USB Sniffer sink, which accepts wrapped USB events and passes them to our event sinks. """

        # Start off with an empty array of packet sinks -- sinks should be registered by calling ``.register_sink`.
        self._sinks = []
        super().__init__()


    def register_sink(self, sink):
        """ Registers a USBEventSink to receive any USB events. """

        self._sinks.append(sink)


    def emit_usb_packet(self, ts, buf, flags):
        for sink in self._sinks:
            sink.handle_usb_packet(ts, buf, flags)


    def handle_packet(self, buf):
        """ Separates the input flags from the core meta-data extracted from the PW USB packet. """
        self.emit_usb_packet(buf['timestamp'], bytearray(buf['contents']), buf['flags'])


class USBSimplePrintSink(USBEventSink):
    """ Most basic sink for USB events: report them directly to the console. """

    import crcmod
    data_crc = staticmethod(crcmod.mkCrcFun(0x18005))

    def __init__(self, highspeed):
        self.frameno = None
        self.subframe = 0
        self.highspeed = highspeed

        self.last_ts_frame = 0

        self.last_ts_print = 0
        self.last_ts_pkt = 0
        self.ts_base = 0
        self.ts_roll_cyc = 2**24


    def handle_usb_packet(self, ts, buf, flags):
        CRC_BAD = 1
        CRC_GOOD = 2
        CRC_NONE = 3
        crc_check = CRC_NONE
        
        ts_delta_pkt = ts - self.last_ts_pkt
        self.last_ts_pkt = ts

        if ts_delta_pkt < 0:
            self.ts_base += self.ts_roll_cyc

        ts += self.ts_base

        suppress = False

        msg = ""

        if len(buf) != 0:
            pid = buf[0] & 0xF
            if (buf[0] >> 4) ^ 0xF != pid:
                msg += "Err - bad PID of %02x" % pid
            elif pid == 0x5:
                if len(buf) < 3:
                    msg += "RUNT frame"
                else:
                    frameno = buf[1] | (buf[2] << 8) & 0x7
                    if self.frameno == None:
                        self.subframe = None
                    else:
                        if self.subframe == None:
                            if frameno == (self.frameno + 1) & 0xFF:
                                self.subframe = 0 if self.highspeed else None
                        else:
                            self.subframe += 1
                            if self.subframe == 8:
                                if frameno == (self.frameno + 1)&0xFF:
                                    self.subframe = 0
                                else:
                                    msg += "WTF Subframe %d" % self.frameno
                                    self.subframe = None
                            elif self.frameno != frameno:
                                msg += "WTF frameno %d" % self.frameno
                                self.subframe = None
                    
                    self.frameno = frameno
                                
                    self.last_ts_frame = ts
                    suppress = True
                    msg += "Frame %d.%c" % (frameno, '?' if self.subframe == None else "%d" % self.subframe)
            elif pid in [0x3, 0xB, 0x7]:
                n = {3:0, 0xB:1, 0x7:2}[pid]

                msg += "DATA%d: %s" % (n,hd(buf[1:]))

                if len(buf) > 2:
                    calc_check = self.data_crc(buf[1:-2])^0xFFFF 
                    pkt_check = buf[-2] | buf[-1] << 8

                    if calc_check != pkt_check:
                        msg += "\tUnexpected ERR CRC"

            elif pid == 0xF:
                msg += "MDATA: %s" % hd(buf[1:])
            elif pid in [0x01, 0x09, 0x0D, 0x04]:
                if pid == 1:
                    name = "OUT"
                elif pid == 9:
                    name = "IN"
                elif pid == 0xD:
                    name = "SETUP"
                elif pid == 0x04:
                    name = "PING"
                if len(buf) < 3:
                    msg += "RUNT: %s %s" % (name, " ".join("%02x" % i for i in buf))
                else:

                    addr = buf[1] & 0x7F
                    endp = (buf[2] & 0x7) << 1 | buf[1] >> 7

                    msg += "%-5s: %d.%d" % (name, addr, endp)
            elif pid == 2:
                msg += "ACK"
            elif pid == 0xA:
                msg += "NAK"
            elif pid == 0xE:
                msg += "STALL"
            elif pid == 0x6:
                msg += "NYET"
            elif pid == 0xC:
                msg += "PRE-ERR"
                pass
            elif pid == 0x8:
                msg += "SPLIT"
                pass
            else:
                msg += "WUT"

        if not suppress:
            crc_char_d = {
                CRC_BAD: '!',
                CRC_GOOD: 'C',
                CRC_NONE: ' '
            }

            flag_field = "[  %s%s%s%s]" % (
                'B' if flags & 0x08 else ' ',   # vbus_valid
                'e' if flags & 0x04 else ' ',   # sess_end
                'V' if flags & 0x02 else ' ',   # sess_valid
                'E' if flags & 0x01 else ' ')   # rx_error
            delta_subframe = ts - self.last_ts_frame
            delta_print = ts - self.last_ts_print
            self.last_ts_print = ts
            RATE=60.0e6

            subf_print = ''
            frame_print = ''

            if self.frameno != None:
                frame_print = "%3d" % self.frameno

            if self.subframe != None:
                subf_print = ".%d" % self.subframe

            print ("%s %10.6f d=%10.6f [%3s%2s +%7.3f] [%3d] %s " % (
                    flag_field, ts/RATE, (delta_print)/RATE,
                    frame_print, subf_print, delta_subframe/RATE * 1E6,
                    len(buf), msg))


