# Welcome to Batcontrol Documentation! 🔋

Batcontrol is an intelligent battery management system that optimizes your home battery usage based on dynamic electricity pricing, solar forecasts, and consumption patterns. This documentation will guide you through setup, configuration, and integration with your home energy system.

## 🚀 Getting Started

### New to Batcontrol?
- **[How Batcontrol Works](getting-started/how-batcontrol-works.md)** - Understand the system architecture and logic
- **[Main Configuration](configuration/batcontrol-configuration.md)** - Basic system settings and logging

### Installation
- See the **[Installation Guide](getting-started/installation.md)** for Docker, docker-compose, local Python, and Home Assistant add-on setup
- **Home Assistant Add-on**: Install via the [batcontrol_ha_addon repository](https://github.com/MaStr/batcontrol_ha_addon)

### Quick Setup Guide
1. Start with the [Main Configuration](configuration/batcontrol-configuration.md) for basic system settings
2. Configure your [Inverter](configuration/inverter-configuration.md) (Fronius GEN24, Fronius Modbus, or MQTT supported)
3. Set up [Dynamic Tariff Provider](configuration/dynamic-tariff-provider.md) for pricing data
4. Configure [Solar Forecast](configuration/solar-forecast.md) for PV predictions
5. Set up [Consumption Forecast](configuration/consumption-forecast.md) for load predictions

## ⚙️ Core Configuration

### Essential Components
| Component | Description | Documentation |
|-----------|-------------|---------------|
| **Inverter** | Connect to your battery inverter | [Inverter Configuration](configuration/inverter-configuration.md) |
| **Dynamic Tariff** | Get real-time electricity prices | [Dynamic Tariff Provider](configuration/dynamic-tariff-provider.md) |
| **Solar Forecast** | Predict solar energy production | [Solar Forecast](configuration/solar-forecast.md) |
| **Consumption Forecast** | Predict energy consumption | [Consumption Forecast](configuration/consumption-forecast.md) |

### Battery Control Logic
| Feature | Description | Documentation |
|---------|-------------|---------------|
| **Basic Control** | Simple price-based charging/discharging | [Main Configuration](configuration/batcontrol-configuration.md) |
| **Expert Mode** | Advanced control with custom logic | [Battery Control Expert](features/battery-control-expert.md) |
| **Peak Shaving** | Spread PV battery charging over the day | [Peak Shaving](features/peak-shaving.md) |
| **Price Calculations** | How price differences are calculated | [Price Difference Calculation](features/price-difference-calculation.md) |

## 🔌 Integrations

### External Systems
| Integration | Purpose | Documentation |
|-------------|---------|---------------|
| **evcc** | Electric vehicle charging coordination | [evcc Connection](integrations/evcc-connection.md) |
| **MQTT/Home Assistant** | Home automation integration | [MQTT API](integrations/mqtt-api.md) |
| **MQTT Inverter** | Integrate any battery system via MQTT | [MQTT Inverter](integrations/mqtt-inverter.md) |

## 📋 Configuration Reference

### Supported Hardware
- **Inverters**: Fronius GEN24 series, Fronius Modbus TCP, MQTT inverter bridge
- **Dynamic Tariff Providers**: aWATTar, Tibber, evcc integration, 2 Tariff Providers like Octopus
- **Solar Forecast**: Forecast.Solar, Solar-Prognose.de, evcc integration
- **Consumption Forecast**: CSV-based load profiles

### File Structure
```
config/
├── batcontrol_config.yaml     # Main configuration file
├── load_profile.csv           # Consumption patterns (optional)
└── grafana-overview.json      # Grafana dashboard (optional)
```

## 🛠️ Advanced Topics

### Expert Features
- **[Battery Control Expert](features/battery-control-expert.md)** - Advanced control algorithms
- **[Price Difference Calculation](features/price-difference-calculation.md)** - Custom pricing logic
- **[MQTT API](integrations/mqtt-api.md)** - Complete API reference for home automation

### Monitoring & Debugging
- **[Main Configuration](configuration/batcontrol-configuration.md)** - Logging and debugging options
- **MQTT Topics** - Real-time monitoring via MQTT

## 💡 Tips for Success

1. **Start Simple**: Begin with basic configuration and add integrations gradually
2. **Monitor Logs**: Enable debug logging during initial setup
3. **Test Incrementally**: Verify each component before adding the next
4. **Check Compatibility**: Ensure your inverter model is supported
5. **Backup Settings**: Keep copies of working configurations

## 🆘 Need Help?

- Check the specific configuration page for your component
- Enable debug logging to troubleshoot issues
- Verify network connectivity for external API services
- Ensure correct timezone settings for accurate time-based operations

---

📝 **Documentation Status**: This documentation lives in the [`docs/` folder of the batcontrol repository](https://github.com/MaStr/batcontrol/tree/main/docs). If you find outdated information or need additional details, please open an issue or pull request.

🔗 **Project Repository**: [GitHub - Batcontrol](https://github.com/MaStr/batcontrol)

---

**LLM-friendly versions of this documentation:**
[llms.txt](llms.txt) — [llms-full.txt](llms-full.txt)
