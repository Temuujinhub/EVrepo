#/bin/sh
#This script is used to upload logs and charge records
File1=/usr/local/app/logtcu.tgz
File2=/usr/local/app/log.zip

df >/usr/local/app/log/ramlog.txt
echo "----------------------------------------">>/usr/local/app/log/ramlog.txt
free >>/usr/local/app/log/ramlog.txt
sync
if [ -f "$File1" ]; then
 rm -f $File1
fi
if [ -f "$File2" ]; then
 rm -f $File2
fi

#sleep 1

#Pack data to .tar
tar -zcvf $File1 /savelog/ /usr/local/app/save /usr/local/app/log/ /usr/local/app/cfg/ /usr/local/app/tcuinfo/ /usr/local/app/ocppinfo/ /media/ram/log/ /usr/local/app/appDaemon.log  

#Change .tar to .zip
mv $File1 $File2


