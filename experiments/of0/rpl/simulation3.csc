<?xml version="1.0" encoding="UTF-8"?>
<simconf version="2023090101">
  <simulation>
    <title>My simulation</title>
    <randomseed>123456</randomseed>
    <motedelay_us>1000000</motedelay_us>

    <radiomedium>
      <radio_medium_type>org.contikios.cooja.radiomediums.UDGM</radio_medium_type>
      <transmitting_range>50.0</transmitting_range>
      <interference_range>100.0</interference_range>
      <success_ratio_tx>1.0</success_ratio_tx>
      <success_ratio_rx>1.0</success_ratio_rx>
    </radiomedium>

    <motetype>
      <mote_type>org.contikios.cooja.contikimote.ContikiMoteType</mote_type>
      <identifier>AppNode</identifier>
      <description>Cooja Mote Type #1</description>
      <source>/workspace/experiments/of0/rpl/app.c</source>
      <commands>make app.cooja TARGET=cooja</commands>

      <moteinterface>org.contikios.cooja.interfaces.Position</moteinterface>
      <moteinterface>org.contikios.cooja.interfaces.Battery</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiVib</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiMoteID</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiRS232</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiBeeper</moteinterface>
      <moteinterface>org.contikios.cooja.interfaces.IPAddress</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiRadio</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiButton</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiPIR</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiClock</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiLED</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiCFS</moteinterface>
      <moteinterface>org.contikios.cooja.contikimote.interfaces.ContikiEEPROM</moteinterface>
      <moteinterface>org.contikios.cooja.interfaces.Mote2MoteRelations</moteinterface>
      <moteinterface>org.contikios.cooja.interfaces.MoteAttributes</moteinterface>

      <mote>
        <interface_config>
          <interface_config_type>org.contikios.cooja.interfaces.Position</interface_config_type>
          <pos x="90.69403175626056" y="21.41643825962769" />
        </interface_config>
        <interface_config>
          <interface_config_type>org.contikios.cooja.contikimote.interfaces.ContikiMoteID</interface_config_type>
          <id>1</id>
        </interface_config>
      </mote>

      <mote>
        <interface_config>
          <interface_config_type>org.contikios.cooja.interfaces.Position</interface_config_type>
          <pos x="60.890826153828336" y="81.0206334282748" />
        </interface_config>
        <interface_config>
          <interface_config_type>org.contikios.cooja.contikimote.interfaces.ContikiMoteID</interface_config_type>
          <id>2</id>
        </interface_config>
      </mote>

      <mote>
        <interface_config>
          <interface_config_type>org.contikios.cooja.interfaces.Position</interface_config_type>
          <pos x="50.27036164648134" y="50.59688864429312" />
        </interface_config>
        <interface_config>
          <interface_config_type>org.contikios.cooja.contikimote.interfaces.ContikiMoteID</interface_config_type>
          <id>3</id>
        </interface_config>
      </mote>
    </motetype>

    <plugins>
      <plugin>
        <plugin_type>org.contikios.cooja.plugins.ScriptRunner</plugin_type>
        <active>true</active>
        <control>true</control>
        <plugin_config>
          <script>
            TIMEOUT(30000);
            log.testOK();
          </script>
        </plugin_config>
      </plugin>
    </plugins>

  </simulation>
</simconf>
