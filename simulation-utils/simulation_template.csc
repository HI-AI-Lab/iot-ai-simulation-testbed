<?xml version='1.0' encoding='utf-8'?>
<simconf version="2023090101">
  <simulation>
    <title>My simulation</title>
    <randomseed>123456</randomseed>
    <motedelay_us>1000000</motedelay_us>
    <radiomedium>
      org.contikios.cooja.radiomediums.UDGM
      <transmitting_range>50.0</transmitting_range>
      <interference_range>100.0</interference_range>
      <success_ratio_tx>1.0</success_ratio_tx>
      <success_ratio_rx>1.0</success_ratio_rx>
    </radiomedium>

    <!-- MOTETYPE MUST BE INSIDE <simulation> -->
    <motetype>
      org.contikios.cooja.contikimote.ContikiMoteType
      <description>Cooja Mote Type #1</description>
      <!-- Keep [CONFIG_DIR] literal; Cooja resolves it -->
      <source>[CONFIG_DIR]/app.c</source>
      <commands>$(MAKE) -j$(CPUS) app.cooja TARGET=cooja</commands>

      <moteinterface>org.contikios.cooja.interfaces.Position</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiMoteID</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiRS232</moteinterface>
      <moteinterface>org.contikios.cooja.interfaces.IPAddress</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiRadio</moteinterface>

      <!-- Single placeholder mote (will be replaced by generator) -->
      <mote>
        <interface_config>
          org.contikios.cooja.interfaces.Position
          <x>0.0</x>
          <y>0.0</y>
          <z>0.0</z>
        </interface_config>
        <interface_config>
          org.contikios.cooja.contikimote.interfaces.ContikiMoteID
          <id>1</id>
        </interface_config>
      </mote>
    </motetype>
  </simulation>

  <plugin control="true">
    org.contikios.cooja.plugins.ScriptRunner
    <plugin_config>
      <!-- The generator will overwrite this absolute path -->
      <scriptfile>[CONFIG_DIR]/simulation.js</scriptfile>
      <active>true</active>
    </plugin_config>
  </plugin>
</simconf>
