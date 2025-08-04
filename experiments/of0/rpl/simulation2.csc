<plugin control="true">
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
