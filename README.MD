**Optimize your Electricity cost by re-charging your PV Battery when Electricity is cheap and there is not enough Solarpower available.**

To include batcontrol in home assistant please use the repository: https://github.com/muexxl/ha_addons/

# Preconditions: 

1. You have a PV Installation with a BYD Battery and a Fronius inverter  
1. You have a Tibber or aWattar contract with hourly electricity pricing. (Use this link if you want 50€ Bonus on sign up https://invite.tibber.com/x8ci52nj.)  

# Local installation
## Preparations:

1. verify the credentials of your inverter for customer or technician
1. get your tibber API key from https://developer.tibber.com/
1. create your batcontrol_config.yaml in the config folder
1. customize your load profile or use the default load profile

## Install:
```
git clone https://github.com/muexxl/batcontrol.git  
cd batcontrol  
virtualenv venv  
source venv/bin/activate  
pip install -r requirements.txt  
```

## run
```
cd /PATH/TO/batcontrol
source venv/bin/activate
python batcontrol.py
```
# Docker usage

# Preparations

```
mkdir -p ./config -p ./logs
```

- Download the the latest [batcontrol_config.yaml](https://raw.githubusercontent.com/muexxl/batcontrol/refs/heads/main/config/batcontrol_config_dummy.yaml) sample, adjust and place it to config/batcontrol_config.yaml.

- Use the default load_profile (automatically) or create your own.-

# Plain Docker

```
docker run -d \
  --name batcontrol \
  -v /path/to/config:/app/config \
  -v /path/to/logs:/app/logs \
  mastr950/batcontrol:latest
```

# Docker-compose example

Create docker-compose.yml with the following content:

```
version: '3.8'

services:
  batcontrol:
    image: mastr950/batcontrol:latest
    volumes:
      - ./config:/app/config
      - ./logs:/app/logs
    restart: unless-stopped
```

Then start the container using `docker-compose up -d`.