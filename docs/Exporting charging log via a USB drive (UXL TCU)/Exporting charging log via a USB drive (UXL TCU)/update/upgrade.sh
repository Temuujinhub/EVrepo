#!/bin/sh


CURPATH=$(cd "$(dirname "$0")"; pwd)
echo "current path is : $CURPATH" >/dev/ttyS0
cd $CURPATH

sleep 5
echo "-----------cmd lsusb -----------------------------" >>/usr/local/app/log/ilitek.txt
lsusb >/usr/local/app/log/ilitek.txt

echo "-----------ll /dev/input/-----------------------------" >>/usr/local/app/log/ilitek.txt
ll /dev/input/ >>/usr/local/app/log/ilitek.txt

ll /dev/hidraw0 >>/usr/local/app/log/ilitek.txt

cp ./logpack.sh /usr/local/app/logpack.sh
chmod +x /usr/local/app/logpack.sh

sync

DATE=$(date +%Y-%m-%d-%H-%M-%S)



/usr/local/app/logpack.sh

cd ./file

mv /usr/local/app/log.zip ./$DATE.zip
