<?xml version="1.0" encoding="UTF-8"?>
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

    <motetype>
      org.contikios.cooja.contikimote.ContikiMoteType
      <identifier>AppNode</identifier>
      <description>Cooja Mote Type #1</description>
      <source>/workspace/experiments/of0/rpl/app.c</source>
      <commands>make app.cooja TARGET=cooja</commands>
      <moteinterface>org.contikios.cooja.interfaces.Position</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiMoteID</moteinterface>
    </motetype>

    <motes>
      <mote>
        <motetype_identifier>AppNode</motetype_identifier>
        <interface_config>
          org.contikios.cooja.interfaces.Position
          <pos x="90.6" y="21.4" />
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
          <pos x="60.8" y="81.0" />
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
          <pos x="50.2" y="50.5" />
        </interface_config>
        <interface_config>
          org.contikios.cooja.contikimote.interfaces.ContikiMoteID
          <id>3</id>
        </interface_config>
      </mote>
    </motes>

    <plugins>
      <plugin>
        org.contikios.cooja.plugins.ScriptRunner
        <active>true</active>
        <control>true</control>
        <plugin_config>
          <script><![CDATA[
            log.log("Simulation started\n");
            TIMEOUT(30000);
            log.testOK();
          ]]></script>
        </plugin_config>
      </plugin>
    </plugins>
  </simulation>
</simconf>
