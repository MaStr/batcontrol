FROM alpine:3.20

RUN mkdir /batcontrol
WORKDIR /batcontrol
RUN apk add --no-cache \
            python3 \
            py3-numpy \
            py3-pandas\
            py3-yaml\
            py3-requests\
            py3-paho-mqtt


COPY *.py ./
COPY default_load_profile.csv ./config/load_profile.csv
COPY default_load_profile.csv ./
COPY config ./config
COPY dynamictariff ./dynamictariff
COPY inverter ./inverter
COPY forecastconsumption ./forecastconsumption
COPY forecastsolar ./forecastsolar
COPY logfilelimiter ./logfilelimiter

CMD [ "./batcontrol.py" ]
