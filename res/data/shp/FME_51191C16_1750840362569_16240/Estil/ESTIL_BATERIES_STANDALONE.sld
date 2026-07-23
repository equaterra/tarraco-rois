<?xml version="1.0" encoding="UTF-8"?><sld:StyledLayerDescriptor xmlns="http://www.opengis.net/sld" xmlns:sld="http://www.opengis.net/sld" xmlns:gml="http://www.opengis.net/gml" xmlns:ogc="http://www.opengis.net/ogc" version="1.0.0">
  <sld:NamedLayer>
    <sld:Name>test_v2</sld:Name>
    <sld:UserStyle>
      <sld:Name>test_v2</sld:Name>
      <sld:FeatureTypeStyle>
        <sld:Name>name</sld:Name>
        <sld:Rule>
          <sld:Name>Autoritzat</sld:Name>
          <sld:Title>Autoritzat</sld:Title>
          <ogc:Filter>
            <ogc:PropertyIsEqualTo>
              <ogc:PropertyName>ESTAT</ogc:PropertyName>
              <ogc:Literal>Autoritzat</ogc:Literal>
            </ogc:PropertyIsEqualTo>
          </ogc:Filter>
          <sld:PolygonSymbolizer>
            <sld:Fill>
              <sld:GraphicFill>
                <sld:Graphic>
                  <sld:Mark>
                    <sld:WellKnownName>shape://slash</sld:WellKnownName>
                    <sld:Stroke>
                      <sld:CssParameter name="stroke">#00c5ff</sld:CssParameter>
                    </sld:Stroke>
                  </sld:Mark>
                  <sld:Size>9</sld:Size>
                </sld:Graphic>
              </sld:GraphicFill>
            </sld:Fill>
            <sld:Stroke>
              <sld:CssParameter name="stroke">#6e6e6e</sld:CssParameter>
              <sld:CssParameter name="stroke-width">1.5</sld:CssParameter>
            </sld:Stroke>
          </sld:PolygonSymbolizer>
        </sld:Rule>
        <sld:Rule>
          <sld:Name>Autoritzat pel Ministerio</sld:Name>
          <sld:Title>Autoritzat pel Ministerio</sld:Title>
          <ogc:Filter>
            <ogc:PropertyIsEqualTo>
              <ogc:PropertyName>ESTAT</ogc:PropertyName>
              <ogc:Literal>Autoritzat pel Ministerio</ogc:Literal>
            </ogc:PropertyIsEqualTo>
          </ogc:Filter>
          <sld:PolygonSymbolizer>
            <sld:Fill>
              <sld:GraphicFill>
                <sld:Graphic>
                  <sld:Mark>
                    <sld:WellKnownName>shape://slash</sld:WellKnownName>
                    <sld:Stroke>
                      <sld:CssParameter name="stroke">#00c5ff</sld:CssParameter>
                    </sld:Stroke>
                  </sld:Mark>
                  <sld:Size>9</sld:Size>
                </sld:Graphic>
              </sld:GraphicFill>
            </sld:Fill>
            <sld:Stroke>
              <sld:CssParameter name="stroke">#FFAA00</sld:CssParameter>
              <sld:CssParameter name="stroke-width">1.5</sld:CssParameter>
            </sld:Stroke>
          </sld:PolygonSymbolizer>
        </sld:Rule>
        <sld:Rule>
          <sld:Name>En tramitació</sld:Name>
          <sld:Title>En tramitació</sld:Title>
          <ogc:Filter>
            <ogc:PropertyIsEqualTo>
              <ogc:PropertyName>ESTAT</ogc:PropertyName>
              <ogc:Literal>En tramitació</ogc:Literal>
            </ogc:PropertyIsEqualTo>
          </ogc:Filter>
          <sld:PolygonSymbolizer>
            <sld:Fill>
              <sld:GraphicFill>
                <sld:Graphic>
                  <sld:Mark>
                    <sld:WellKnownName>shape://slash</sld:WellKnownName>
                    <sld:Stroke>
                      <sld:CssParameter name="stroke">#C32DFE</sld:CssParameter>
                    </sld:Stroke>
                  </sld:Mark>
                  <sld:Size>9</sld:Size>
                </sld:Graphic>
              </sld:GraphicFill>
            </sld:Fill>
            <sld:Stroke>
              <sld:CssParameter name="stroke">#6e6e6e</sld:CssParameter>
              <sld:CssParameter name="stroke-width">1.5</sld:CssParameter>
            </sld:Stroke>
          </sld:PolygonSymbolizer>
        </sld:Rule>
        <sld:Rule>
          <sld:Name>En tramitació al Ministerio</sld:Name>
          <sld:Title>En tramitació al Ministerio</sld:Title>
          <ogc:Filter>
            <ogc:PropertyIsEqualTo>
              <ogc:PropertyName>ESTAT</ogc:PropertyName>
              <ogc:Literal>En tramitació al Ministerio</ogc:Literal>
            </ogc:PropertyIsEqualTo>
          </ogc:Filter>
          <sld:PolygonSymbolizer>
            <sld:Fill>
              <sld:GraphicFill>
                <sld:Graphic>
                  <sld:Mark>
                    <sld:WellKnownName>shape://slash</sld:WellKnownName>
                    <sld:Stroke>
                      <sld:CssParameter name="stroke">#C32DFE</sld:CssParameter>
                    </sld:Stroke>
                  </sld:Mark>
                  <sld:Size>9</sld:Size>
                </sld:Graphic>
              </sld:GraphicFill>
            </sld:Fill>
            <sld:Stroke>
              <sld:CssParameter name="stroke">#FFAA00</sld:CssParameter>
              <sld:CssParameter name="stroke-width">1.5</sld:CssParameter>
            </sld:Stroke>
          </sld:PolygonSymbolizer>
        </sld:Rule>
      </sld:FeatureTypeStyle>
    </sld:UserStyle>
  </sld:NamedLayer>
</sld:StyledLayerDescriptor>

