-- Wave 3.5 (Nov 10, 2026): state assisted-living license rosters beyond Michigan. Terms/robots checked Nov 10.
insert into raw.source (source_id, name, category, access_method, base_url, refresh_cadence, notes) values
  ('nc_dhsr', 'North Carolina DHSR Adult Care Homes list',        'ownership', 'file', 'https://info.ncdhhs.gov/dhsr/reports.htm', 'monthly', 'Ahlist.xlsx; licensee legal name; DHSR disclaimer permits copy/distribute'),
  ('tx_hhsc', 'Texas HHSC Assisted Living Facility directory',     'ownership', 'file', 'https://apps.hhs.texas.gov/providers/directories/al.xlsx', 'weekly', 'Owner_ = licensee; www.hhs.texas.gov page 403s our crawler, terms page unread'),
  ('ny_doh',  'New York DOH adult care facilities (Socrata)',      'ownership', 'api',  'https://health.data.ny.gov/d/vn5v-hh5r', 'weekly', 'Adult Home + Enriched Housing; operator_name; ALR beds from 2g9y-7kqm'),
  ('ia_dial', 'Iowa DIAL Assisted Living Programs (data.iowa.gov)', 'ownership', 'file', 'https://data.iowa.gov/d/562', 'irregular', 'CC BY 4.0; no licensee column (buildings only)'),
  ('pa_dhs',  'Pennsylvania DHS Licensed Personal Care Homes',     'ownership', 'api',  'https://data.pa.gov/d/pqf4-d4xn', 'weekly', 'Public domain; no licensee or license number (buildings only)'),
  ('oh_odh',  'Ohio ODH residential care facilities',               'ownership', 'manual', 'https://publicapps.odh.ohio.gov/eid/default.aspx', 'manual', 'ASP.NET export form only; manual export'),
  ('il_idph', 'Illinois IDPH assisted living / shared housing',     'ownership', 'manual', 'https://llcs.dph.illinois.gov/s/', 'manual', 'Salesforce search portal only; FOIA for roster'),
  ('il_hfs_slf', 'Illinois HFS Supportive Living Facilities',       'ownership', 'manual', 'https://hfs.illinois.gov/medicalprograms/slf.html', 'monthly', 'OperationalSites.docx (Company column); not yet loaded'),
  ('ks_kdads', 'Kansas KDADS adult care home directory',            'ownership', 'manual', 'https://www.kdads.ks.gov', 'manual', 'Site returns 403 to our crawler; records request')
on conflict (source_id) do nothing;
