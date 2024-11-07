FROM alpine:3.20

ARG VERSION
ARG GIT_SHA

LABEL version="${VERSION}"
LABEL git-sha="${GIT_SHA}"
LABEL description="This is a Docker image for the BatControl project."
LABEL maintainer="matthias.strubel@aod-rpg.de"

ENV BATCONTROL_VERSION=${VERSION}
ENV BATCONTROL_GIT_SHA=${GIT_SHA}

RUN mkdir /app
WORKDIR /app
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
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

VOLUME [ "/app/logs" ]

CMD [ "/app/entrypoint.sh" ]
