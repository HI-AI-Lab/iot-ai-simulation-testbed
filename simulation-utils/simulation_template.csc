<?xml version="1.0" encoding="UTF-8"?>
<simconf version="2023090101">
  <simulation>
    <title>Randomized Simulation Template</title>
    <randomseed>123456</randomseed>
    <motedelay_us>1000000</motedelay_us>
    <radiomedium>
      org.contikios.cooja.radiomediums.UDGM
      <transmitting_range>50.0</transmitting_range>
      <interference_range>100.0</interference_range>
      <success_ratio_tx>1.0</success_ratio_tx>
      <success_ratio_rx>1.0</success_ratio_rx>
    </radiomedium>
  </simulation>

  <motetype>
    org.contikios.cooja.contikimote.ContikiMoteType
    <description>Template Mote</description>
    <source>[CONFIG_DIR]/app.c</source>
    <commands>$(MAKE) -j$(CPUS) app.cooja TARGET=cooja</commands>

    <!-- Minimal interfaces needed for headless + logging -->
    <moteinterface>org.contikios.cooja.interfaces.Position</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiMoteID</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiRS232</moteinterface>
    <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiRadio</moteinterface>
    <moteinterface>org.contikios.cooja.interfaces.IPAddress</moteinterface>

    <!-- Single template mote (will be cloned by Python) -->
    <mote>
      <interface_config>
        org.contikios.cooja.interfaces.Position
        <pos x="0.0" y="0.0" />
      </interface_config>
      <interface_config>
        org.contikios.cooja.contikimote.interfaces.ContikiMoteID
        <id>1</id>
      </interface_config>
    </mote>
  </motetype>

  <plugin control="true">
    org.contikios.cooja.plugins.ScriptRunner
    <plugin_config>
      <scriptfile>[CONFIG_DIR]/simulation.js</scriptfile>
      <active>true</active>
    </plugin_config>
  </plugin>
</simconf>
