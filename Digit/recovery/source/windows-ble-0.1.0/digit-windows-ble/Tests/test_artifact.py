import pathlib,re,unittest,xml.etree.ElementTree as ET
ROOT=pathlib.Path(__file__).resolve().parents[1]
class ArtifactTests(unittest.TestCase):
 def test_targeted_notifications_only(self):
  s=(ROOT/'Windows/Controller.cs').read_text()
  calls=re.findall(r'\.NotifyValueAsync\(([^;]+)\)',s)
  self.assertEqual(calls,['bytes.AsBuffer(),client).AsTask('])
 def test_required_security_and_no_reserved_publication(self):
  s=(ROOT/'Windows/Controller.cs').read_text()
  self.assertIn('adapter.IsPeripheralRoleSupported',s)
  self.assertIn('HidProfile.Uuid(0x1812)',s);self.assertIn('HidProfile.Uuid(0x180F)',s)
  self.assertNotIn('HidProfile.Uuid(0x180A)',s);self.assertNotIn('HidProfile.Uuid(0x2902)',s)
  self.assertGreaterEqual(s.count('GattProtectionLevel.EncryptionRequired'),5)
  self.assertIn('Clients().Length==1',s);self.assertIn('MaxNotificationSize>=8',s)
 def test_report_map_input_size_and_reference(self):
  s=(ROOT/'Windows/HidProfile.cs').read_text()
  descriptor=s.split('ReportMap = {')[1].split('};')[0]
  data=bytes(int(x,16) for x in re.findall(r'0x([0-9A-Fa-f]{2})',descriptor))
  i=0;size=count=report=0;bits={};collections=0
  while i<len(data):
   prefix=data[i];i+=1;n=(0,1,2,4)[prefix&3];v=int.from_bytes(data[i:i+n],'little');i+=n
   tag=prefix&0xFC
   if tag==0x74:size=v
   elif tag==0x94:count=v
   elif tag==0x84:report=v
   elif tag==0x80:bits[report]=bits.get(report,0)+size*count
   elif tag==0xA0:collections+=1
   elif tag==0xC0:collections-=1
  self.assertEqual(bits,{1:64});self.assertEqual(collections,0)
  self.assertIn('ReportReference = { 0x01, 0x01 }',s)
 def test_projects_xml(self):
  ET.parse(ROOT/'Windows/DigitWindowsBLE.csproj');ET.parse(ROOT/'Windows/app.manifest')
if __name__=='__main__':unittest.main()
