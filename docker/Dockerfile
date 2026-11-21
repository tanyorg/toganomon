# toganomon worker: go-around detection once a minute and the airport's
# METAR/TAF from NOAA. Build context is the repository root:
#   docker build -f docker/Dockerfile .
FROM python:3.13-alpine

RUN addgroup -S toganomon && adduser -S -G toganomon -H -h /var/lib/toganomon toganomon \
 && install -d -o toganomon -g toganomon -m 0755 /var/lib/toganomon /srv/toganomon

COPY toganomon.py fetch_wx.py airports.json /app/
COPY docker/entrypoint.sh /app/entrypoint.sh

USER toganomon
# state + history (keep on a volume), and what the web server serves
VOLUME ["/var/lib/toganomon", "/srv/toganomon"]
CMD ["/bin/sh", "/app/entrypoint.sh"]
