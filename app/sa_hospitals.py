"""
Public hospitals across South Africa's nine provinces, so referrals can go to a
realistic destination. Each entry: (id, name, town, province).

They are referral destinations only: no staff, patients or data from these
hospitals are in SKAI, and SKAI does not connect to them. Names are from
general knowledge of well-known public hospitals; check the current Department
of Health facility list before relying on them.
"""

HOSPITALS = [
    # Gauteng
    ("gp-chris-hani-baragwanath", "Chris Hani Baragwanath Academic Hospital", "Soweto", "Gauteng"),
    ("gp-charlotte-maxeke", "Charlotte Maxeke Johannesburg Academic Hospital", "Johannesburg", "Gauteng"),
    ("gp-steve-biko", "Steve Biko Academic Hospital", "Pretoria", "Gauteng"),
    ("gp-george-mukhari", "Dr George Mukhari Academic Hospital", "Ga-Rankuwa", "Gauteng"),
    ("gp-tembisa", "Tembisa Provincial Tertiary Hospital", "Tembisa", "Gauteng"),
    ("gp-kalafong", "Kalafong Provincial Tertiary Hospital", "Atteridgeville", "Gauteng"),
    ("gp-tshwane-district", "Tshwane District Hospital", "Pretoria", "Gauteng"),
    ("gp-mamelodi", "Mamelodi Regional Hospital", "Mamelodi", "Gauteng"),
    # Western Cape
    ("wc-groote-schuur", "Groote Schuur Hospital", "Cape Town", "Western Cape"),
    ("wc-tygerberg", "Tygerberg Hospital", "Bellville", "Western Cape"),
    ("wc-new-somerset", "New Somerset Hospital", "Cape Town", "Western Cape"),
    ("wc-khayelitsha", "Khayelitsha District Hospital", "Khayelitsha", "Western Cape"),
    ("wc-mitchells-plain", "Mitchells Plain Hospital", "Mitchells Plain", "Western Cape"),
    ("wc-george", "George Hospital", "George", "Western Cape"),
    ("wc-paarl", "Paarl Hospital", "Paarl", "Western Cape"),
    ("wc-worcester", "Worcester Hospital", "Worcester", "Western Cape"),
    # KwaZulu-Natal
    ("kzn-inkosi-albert-luthuli", "Inkosi Albert Luthuli Central Hospital", "Durban", "KwaZulu-Natal"),
    ("kzn-king-edward", "King Edward VIII Hospital", "Durban", "KwaZulu-Natal"),
    ("kzn-addington", "Addington Hospital", "Durban", "KwaZulu-Natal"),
    ("kzn-prince-mshiyeni", "Prince Mshiyeni Memorial Hospital", "Umlazi", "KwaZulu-Natal"),
    ("kzn-greys", "Grey's Hospital", "Pietermaritzburg", "KwaZulu-Natal"),
    ("kzn-edendale", "Edendale Hospital", "Pietermaritzburg", "KwaZulu-Natal"),
    ("kzn-ngwelezana", "Ngwelezana Hospital", "Empangeni", "KwaZulu-Natal"),
    # Eastern Cape
    ("ec-livingstone", "Livingstone Hospital", "Gqeberha", "Eastern Cape"),
    ("ec-dora-nginza", "Dora Nginza Hospital", "Gqeberha", "Eastern Cape"),
    ("ec-frere", "Frere Hospital", "East London", "Eastern Cape"),
    ("ec-cecilia-makiwane", "Cecilia Makiwane Hospital", "Mdantsane", "Eastern Cape"),
    ("ec-nelson-mandela", "Nelson Mandela Academic Hospital", "Mthatha", "Eastern Cape"),
    # Free State
    ("fs-universitas", "Universitas Academic Hospital", "Bloemfontein", "Free State"),
    ("fs-pelonomi", "Pelonomi Tertiary Hospital", "Bloemfontein", "Free State"),
    ("fs-bongani", "Bongani Regional Hospital", "Welkom", "Free State"),
    ("fs-boitumelo", "Boitumelo Regional Hospital", "Kroonstad", "Free State"),
    ("fs-dihlabeng", "Dihlabeng Regional Hospital", "Bethlehem", "Free State"),
    ("fs-manapo", "Manapo Hospital", "Phuthaditjhaba", "Free State"),
    # Limpopo
    ("lp-pietersburg", "Pietersburg Provincial Hospital", "Polokwane", "Limpopo"),
    ("lp-mankweng", "Mankweng Hospital", "Mankweng", "Limpopo"),
    ("lp-st-ritas", "St Rita's Hospital", "Glen Cowie", "Limpopo"),
    ("lp-letaba", "Letaba Hospital", "Tzaneen", "Limpopo"),
    ("lp-tshilidzini", "Tshilidzini Hospital", "Thohoyandou", "Limpopo"),
    ("lp-mokopane", "Mokopane Hospital", "Mokopane", "Limpopo"),
    # Mpumalanga
    ("mp-rob-ferreira", "Rob Ferreira Hospital", "Mbombela", "Mpumalanga"),
    ("mp-witbank", "Witbank Hospital", "eMalahleni", "Mpumalanga"),
    ("mp-ermelo", "Ermelo Hospital", "Ermelo", "Mpumalanga"),
    ("mp-themba", "Themba Hospital", "KaBokweni", "Mpumalanga"),
    ("mp-middelburg", "Middelburg Hospital", "Middelburg", "Mpumalanga"),
    # North West
    ("nw-tshepong", "Klerksdorp-Tshepong Hospital Complex", "Klerksdorp", "North West"),
    ("nw-mahikeng", "Mahikeng Provincial Hospital", "Mahikeng", "North West"),
    ("nw-job-shimankana-tabane", "Job Shimankana Tabane Hospital", "Rustenburg", "North West"),
    ("nw-joe-morolong", "Joe Morolong Memorial Hospital", "Vryburg", "North West"),
    ("nw-potchefstroom", "Potchefstroom Hospital", "Potchefstroom", "North West"),
    # Northern Cape
    ("nc-robert-sobukwe", "Robert Mangaliso Sobukwe Hospital", "Kimberley", "Northern Cape"),
    ("nc-harry-surtie", "Dr Harry Surtie Hospital", "Upington", "Northern Cape"),
    ("nc-kuruman", "Kuruman Hospital", "Kuruman", "Northern Cape"),
    ("nc-tshwaragano", "Tshwaragano Hospital", "Batlharos", "Northern Cape"),
    ("nc-van-niekerk", "Dr Van Niekerk Hospital", "Springbok", "Northern Cape"),
]
