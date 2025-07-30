<?xml version="1.0" encoding="UTF-8"?>
<simulation>
  <title>OF0-TwoNodeTest</title>
  <randomseed>123456</randomseed>
  <tickstopsimulation>900000</tickstopsimulation>
  <speedlimit>0</speedlimit>
  <motedelay_us>10000</motedelay_us>
  <radiomedium>org.contikios.cooja.radiomediums.UDGM</radiomedium>

  <!-- Mote Type: app.cooja -->
  <motetypes>
    <motetype>
      <identifier>AppNode</identifier>
      <description>OF0 App Mote</description>
      <source>app.cooja</source>
      <commands>make app.cooja TARGET=cooja</commands>
    </motetype>
  </motetypes>

  <!-- Mote 1: Root -->
  <mote>
    <motetype_identifier>AppNode</motetype_identifier>
    <interface_config>
      <interface_class>org.contikios.cooja.interfaces.Position</interface_class>
      <x>100.0</x>
      <y>100.0</y>
    </interface_config>
    <interface_config>
      <interface_class>org.contikios.cooja.interfaces.MoteID</interface_class>
      <id>1</id>
    </interface_config>
  </mote>

  <!-- Mote 2: Non-root -->
  <mote>
    <motetype_identifier>AppNode</motetype_identifier>
    <interface_config>
      <interface_class>org.contikios.cooja.interfaces.Position</interface_class>
      <x>120.0</x>
      <y>100.0</y>
    </interface_config>
    <interface_config>
      <interface_class>org.contikios.cooja.interfaces.MoteID</interface_class>
      <id>2</id>
    </interface_config>
  </mote>
</simulation>
