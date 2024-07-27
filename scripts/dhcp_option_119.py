#!/usr/bin/env python3
# Domain Search Option Format generator for Unifi Security Gateway

# Usage: python3 dhcp_option_119.py example.com example.net
import sys

hexes = []
for domain in sys.argv[1:]:
    for label in domain.split('.'):
        hexes.append('%02x' % len(label))
        hexes.extend(['%02x' % ord(char) for char in str.lower(label)])
    hexes.append('00')
print(":".join(hexes))
