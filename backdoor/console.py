#!/usr/bin/env python

# Let's have something like printf,
# but based on a ring buffer in some memory that's not ours.
#
# This is the Python counterpart to the C++ console.h module.
# It can run stand-alone, or via the %console shell command.

__all__ = [
    'console_mainloop', 'ConsoleBuffer',
    'console_address', 'ConsoleOverflowError'
]

import sys, time
from code import *
from dump import *


# Our ring buffer is 64 KiB. The default location comes from
# more guesswork and memsquares. It's 1MB above the default
# pad, still in an area of DRAM that seems very lightly used.

console_address = 0x1e50000

# If console.h appears in shell_builtins.h, we should be sure
# the console_address is always available even in code compiled
# without the whole shell namespace.

defines['console_address'] = console_address


class ConsoleOverflowError(Exception):
    """Raised by ConsoleBuffer.read() in case of FIFO buffer overflow"""
    def __init__(self, next_write, next_read):
        self.next_write = next_write
        self.next_read = next_read
        self.byte_count = (next_write - next_read) & 0xffffffff
        self.unsynchronized = self.byte_count > 0x1000000
        if self.unsynchronized:
            Exception.__init__(self, "Console wasn't synchronized. Previous data lost")
        else:
            Exception.__init__(self, "Buffer overflow, %d bytes lost" % self.byte_count)


class ConsoleBuffer:
    """Reader object for the debugger's console buffer.
    This object caches state to improve performance.
    """
    def __init__(self, d, buffer_address = console_address):
        self.d = d
        self.buffer = buffer_address
        self.next_write = None
        self.next_read = None

    def read(self, max_round_trips = 10):
        """Read all the data we can get from the console quickly.
        If a buffer overflow occurred, raises a ConsoleOverflowError.
        """
        try:
            # Update cached FIFO pointers if needed
            if self.next_write is None or self.next_read is None:
                self.next_write, self.next_read = words_from_string(
                    self.d.read_block(self.buffer + 0x10000, 2))

            byte_count = (self.next_write - self.next_read) & 0xffffffff
            if byte_count > 0xffff:
                # Overflow! Catch up, and leave
                e = ConsoleOverflowError(self.next_write, self.next_read)
                self.next_read = self.next_write
                self.d.poke(self.buffer + 0x10004, self.next_read)
                raise e

            wr16 = self.next_write & 0xffff
            rd16 = self.next_read & 0xffff
            if wr16 > rd16:
                # More data available, and the buffer didn't wrap
                max_read_len = wr16 - rd16
            elif wr16 < rd16:
                # Buffer wrapped; just get the contiguous piece for now
                max_read_len = 0x10000 - rd16
            else:
                # No change; return without updating next_read. Make sure to reload the cache next time.
                assert byte_count == 0
                self.next_read = None
                return ''

            data = read_block(self.d, self.buffer + rd16, max_read_len,
                    max_round_trips=max_round_trips)

            # Acknowledge the amount we actually read
            self.next_read = (self.next_read + len(data)) & 0xffffffff;
            self.d.poke(self.buffer + 0x10004, self.next_read)
            return data

        except:
            # If anything went wrong, invalidate the FIFO pointer cache
            self.next_write = None
            self.next_read = None
            raise


def console_mainloop(d,
    buffer = console_address,
    stdout = sys.stdout,
    log_filename = None,
    spinner_interval = 1.0 / 8
    ):
    """Main loop to forward data from the console buffer to stdout.
    Supports appending to a text log file.
    Also draws a small ASCII spinner to let you know the poll loop is running.
    If it stops, something's crashed.
    """

    log_file = log_filename and open(log_filename, 'a')
    output_timestamp = time.time()
    spinner_chars = '-\\|/'
    spinner_count = 0
    spinner_visible = False
    console_buffer = ConsoleBuffer(d, buffer)

    try:
        while True:
            try:
                data = console_buffer.read()
                now = time.time()

                if data:
                    if spinner_visible:
                        stdout.write('\b')
                        spinner_visible = False
                    stdout.write(data)
                    stdout.flush()
                    output_timestamp = now

                    if log_file:
                        log_file.write(data)
                        log_file.flush()

                elif spinner_interval and now > output_timestamp + spinner_interval:

                    if spinner_visible:
                        stdout.write('\b')

                    spinner_visible = True
                    spinner_count = (spinner_count + 1) % len(spinner_chars)
                    stdout.write(spinner_chars[spinner_count])
                    stdout.flush()
                    output_timestamp = now

            except IOError:
                # The device layer will already complain for us.
                # Wait a tiny bit to let the device cool off...
                time.sleep(0.1)
                continue

            except ConsoleOverflowError, e:
                # Warn loudly that we missed some data
                sys.stderr.write('\n\n======== %s ========\n\n' % e)

    except KeyboardInterrupt:
        return

    finally:
        if log_file:
            log_file.close()


if __name__ == "__main__":
    import remote

    if len(sys.argv) != 1:
        print "usage: %s" % sys.argv[0]
        sys.exit(1)

    console_mainloop(remote.Device())