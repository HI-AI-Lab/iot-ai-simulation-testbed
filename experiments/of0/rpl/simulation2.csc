<?xml version="1.0" encoding="UTF-8"?>
<simconf version="2023090101">
  <simulation>
    <title>OF0 Headless Simulation</title>
    <randomseed>123456</randomseed>
    <motedelay_us>1000000</motedelay_us>

    <radiomedium>
      org.contikios.cooja.radiomediums.UDGM
      <transmitting_range>50.0</transmitting_range>
      <interference_range>100.0</interference_range>
      <success_ratio_tx>1.0</success_ratio_tx>
      <success_ratio_rx>1.0</success_ratio_rx>
    </radiomedium>

    <motetype>
      org.contikios.cooja.contikimote.ContikiMoteType
      <identifier>AppNode</identifier>
      <description>Compile-time App Mote</description>
      <source>[CONFIG_DIR]/app.c</source>
      <commands>make app.cooja TARGET=cooja</commands>
      <moteinterface>org.contikios.cooja.interfaces.Position</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiMoteID</moteinterface>
    </motetype>

    <motes>
      <mote>
        <motetype_identifier>AppNode</motetype_identifier>
        <interface_config>
          org.contikios.cooja.interfaces.Position
          <pos x="10.0" y="20.0"/>
        </interface_config>
        <interface_config>
          org.contikios.cooja.contikimote.interfaces.ContikiMoteID
          <id>1</id>
        </interface_config>
      </mote>
      <mote>
        <motetype_identifier>AppNode</motetype_identifier>
        <interface_config>
          org.contikios.cooja.interfaces.Position
          <pos x="30.0" y="40.0"/>
        </interface_config>
        <interface_config>
          org.contikios.cooja.contikimote.interfaces.ContikiMoteID
          <id>2</id>
        </interface_config>
      </mote>
      <mote>
        <motetype_identifier>AppNode</motetype_identifier>
        <interface_config>
          org.contikios.cooja.interfaces.Position
          <pos x="50.0" y="60.0"/>
        </interface_config>
        <interface_config>
          org.contikios.cooja.contikimote.interfaces.ContikiMoteID
          <id>3</id>
        </interface_config>
      </mote>
    </motes>

    <!-- Dummy plugin that satisfies the simulation controller requirement -->
    <plugin control="true">
      <classname>org.contikios.cooja.plugins.SimulationControl</classname>
    </plugin>

    <!-- Script plugin that manages simulation time and output -->
    <plugin>
      <classname>org.contikios.cooja.plugins.ScriptRunner</classname>
      <plugin_config>
        <script><![CDATA[
          log.log("Simulation started\n");
          TIMEOUT(60000, function() {
            log.log("60 seconds passed. Stopping simulation.\n");
            sim.stop();
          });
        ]]></script>
        <active>true</active>
      </plugin_config>
    </plugin>

  </simulation>
</simconf>
