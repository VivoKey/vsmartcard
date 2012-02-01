#
# Copyright (C) 2011 Dominik Oepen, Frank Morgner
# 
# This file is part of virtualsmartcard.
# 
# virtualsmartcard is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
# 
# virtualsmartcard is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
# 
# You should have received a copy of the GNU General Public License along with
# virtualsmartcard.  If not, see <http://www.gnu.org/licenses/>.

from virtualsmartcard.SmartcardSAM import SAM
from virtualsmartcard.SEutils import ControlReferenceTemplate, Security_Environment
from virtualsmartcard.SWutils import SwError, SW
from virtualsmartcard.ConstantDefinitions import CRT_TEMPLATE, SM_Class
from virtualsmartcard.TLVutils import unpack, bertlv_pack
from virtualsmartcard.SmartcardFilesystem import make_property
from virtualsmartcard.utils import inttostring, hexdump
import virtualsmartcard.CryptoUtils as vsCrypto
from chat import CHAT
import pace

class nPA_AT_CRT(ControlReferenceTemplate):

    PACE_MRZ = 0x01
    PACE_CAN = 0x02
    PACE_PIN = 0x03
    PACE_PUK = 0x04

    def __init__(self):
        ControlReferenceTemplate.__init__(self, CRT_TEMPLATE["AT"])

    def keyref_is_mrz(self):
        if self.keyref == '%c'% self.PACE_MRZ:
            return True
        return False

    def keyref_is_can(self):
        if self.keyref == '%c'% self.PACE_CAN:
            return True
        return False

    def keyref_is_pin(self):
        if self.keyref == '%c'% self.PACE_PIN:
            return True
        return False

    def keyref_is_puk(self):
        if self.keyref == '%c'% self.PACE_PUK:
            return True
        return False

    def parse_SE_config(self, config):
        r = 0x9000
        try:
            ControlReferenceTemplate.parse_SE_config(self, config)
        except SwError as e:
            structure = unpack(config)
            for tlv in structure:
                tag, length, value = tlv
                if tag == 0x7f4c:
                    chat = CHAT(bertlv_pack([[tag, length, value]]))
                    print(chat)
                elif tag == 0x67:
                    self.auxiliary_data = bertlv_pack([[tag, length, value]])
                elif tag == 0x80 or tag == 0x84 or tag == 0x83:
                    # handled by ControlReferenceTemplate.parse_SE_config
                    pass
                elif tag == 0x91:
                    #print "saving eph_pub_key"
                    self.eph_pub_key = value
                else:
                    raise SwError(SW["ERR_REFNOTUSABLE"])

        structure = unpack(config)

        pin_ref_str = '%c'% self.PACE_PIN
        for tlv in structure:
            if [0x83, len(pin_ref_str), pin_ref_str] == tlv:
                if self.sam.counter <= 0:
                    r = 0x63c0
                elif self.sam.counter == 1:
                    r = 0x63c1
                elif self.sam.counter == 2:
                    r = 0x63c2

        return r, ""

class nPA_SE(Security_Environment):
    # TODO call __eac_abort whenever an error occurred

    eac_step = make_property("eac_step", "next step to performed for EAC")

    def __init__(self, MF, SAM):
        Security_Environment.__init__(self, MF, SAM)
        self.at = nPA_AT_CRT() 
        #This breaks support for 3DES
        self.cct.blocklength = 16
        self.cct.algorithm = "CC"
        self.eac_step = 0
        self.sec = None
        self.eac_ctx = None
        self.ssc = 0
        self.ca = "DECVCAeID00102"

    def _set_SE(self, p2, data):
        sw, resp = Security_Environment._set_SE(self, p2, data)
        
        if self.at.algorithm == "PACE":
            self.eac_step = 0
        elif self.at.algorithm == "TA":
            if self.eac_step != 4:
                SwError(SW["ERR_AUTHBLOCKED"])
        elif self.at.algorithm == "CA":
            if self.eac_step != 5:
                SwError(SW["ERR_AUTHBLOCKED"])

        return sw, resp

    def general_authenticate(self, p1, p2, data):
        if (p1, p2) != (0x00, 0x00):
            raise SwError(SW["ERR_INCORRECTPARAMETERS"])

        if   self.eac_step == 0 and self.at.algorithm == "PACE":
            return self.__eac_pace_step1(data)
        elif self.eac_step == 1 and self.at.algorithm == "PACE":
            return self.__eac_pace_step2(data)
        elif self.eac_step == 2 and self.at.algorithm == "PACE":
            return self.__eac_pace_step3(data)
        elif self.eac_step == 3 and self.at.algorithm == "PACE":
            return self.__eac_pace_step4(data)
        elif self.eac_step == 5 and self.at.algorithm == "CA":
            return self.__eac_ca(data)

        raise SwError(SW["ERR_INCORRECTPARAMETERS"])

    def __eac_abort(self):
        pace.EAC_CTX_clear_free(self.eac_ctx)
        self.eac_ctx = None
        pace.PACE_SEC_clear_free(self.sec)
        self.sec = None

    @staticmethod
    def __unpack_general_authenticate(data):
        data_structure = []
        structure = unpack(data)
        for tlv in structure:
            tag, length, value = tlv
            if tag == 0x7c:
                data_structure = value
            else:
                raise SwError(SW["ERR_INCORRECTPARAMETERS"])
        return data_structure

    @staticmethod
    def __pack_general_authenticate(data):
        tlv_data = bertlv_pack(data)
        return bertlv_pack([[0x7c, len(tlv_data), tlv_data]])

    def __eac_pace_step1(self, data):
        tlv_data = nPA_SE.__unpack_general_authenticate(data)
        if  tlv_data != []:
            raise SwError(SW["WARN_NOINFO63"])

        self.__eac_abort()

        self.eac_ctx = pace.EAC_CTX_new()
        if self.at.keyref_is_mrz():
            self.sec = pace.PACE_SEC_new(self.sam.mrz, pace.PACE_MRZ)
        elif self.at.keyref_is_can():
            self.sec = pace.PACE_SEC_new(self.sam.can, pace.PACE_CAN)
        elif self.at.keyref_is_pin():
            if self.sam.counter <= 0:
                print "Must use PUK to unblock"
                raise SwError(SW["WARN_NOINFO63"])
            if self.sam.counter == 1 and not self.sam.active:
                print "Must use CAN to activate"
                return 0x63c1, ""
            self.sec = pace.PACE_SEC_new(self.sam.PIN, pace.PACE_PIN)
            self.sam.counter -= 1
            if self.sam.counter <= 1:
                self.sam.active = False
        elif self.at.keyref_is_puk():
            if self.sam.counter_puk <= 0:
                raise SwError(SW["WARN_NOINFO63"])
            self.sec = pace.PACE_SEC_new(self.sam.puk, pace.PACE_PUK)
            self.sam.counter_puk -= 1
        else:
            raise SwError(SW["ERR_INCORRECTPARAMETERS"])

        ef_card_access = self.mf.select('fid', 0x011c)
        ef_card_access_data = ef_card_access.getenc('data')
        pace.EAC_CTX_init_ef_cardaccess(ef_card_access_data, self.eac_ctx)
        pace.EAC_CTX_init_ca(self.eac_ctx, pace.id_CA_DH_AES_CBC_CMAC_128, 13, None, None)

        nonce = pace.buf2string(pace.PACE_STEP1_enc_nonce(self.eac_ctx, self.sec))
        resp = nPA_SE.__pack_general_authenticate([[0x80, len(nonce), nonce]])

        self.eac_step += 1

        return 0x9000, resp

    def __eac_pace_step2(self, data):
        tlv_data = nPA_SE.__unpack_general_authenticate(data)

        pubkey = pace.buf2string(pace.PACE_STEP3A_generate_mapping_data(self.eac_ctx))

        for tag, length, value in tlv_data:
            if tag == 0x81:
                pace.PACE_STEP3A_map_generator(self.eac_ctx, pace.get_buf(value))
            else:
                raise SwError(SW["ERR_INCORRECTPARAMETERS"])

        self.eac_step += 1

        return 0x9000, nPA_SE.__pack_general_authenticate([[0x82, len(pubkey), pubkey]])

    def __eac_pace_step3(self, data):
        tlv_data = nPA_SE.__unpack_general_authenticate(data)

        self.my_pace_eph_pubkey = pace.PACE_STEP3B_generate_ephemeral_key(self.eac_ctx)
        if not self.my_pace_eph_pubkey:
            pace.print_ossl_err()
            raise SwError(SW["WARN_NOINFO63"])
        eph_pubkey = pace.buf2string(self.my_pace_eph_pubkey)

        for tag, length, value in tlv_data:
            if tag == 0x83:
                self.pace_opp_pub_key = pace.get_buf(value)
                pace.PACE_STEP3B_compute_shared_secret(self.eac_ctx, self.pace_opp_pub_key)
            else:
                raise SwError(SW["ERR_INCORRECTPARAMETERS"])

        self.eac_step += 1

        return 0x9000, nPA_SE.__pack_general_authenticate([[0x84, len(eph_pubkey), eph_pubkey]])

    def __eac_pace_step4(self, data):
        tlv_data = nPA_SE.__unpack_general_authenticate(data)
        pace.PACE_STEP3C_derive_keys(self.eac_ctx)
        my_token = pace.buf2string(pace.PACE_STEP3D_compute_authentication_token(self.eac_ctx, self.pace_opp_pub_key))
        token = ""
        for tag, length, value in tlv_data:
            if tag == 0x85:
                token = value
            else:
                raise SwError(SW["ERR_INCORRECTPARAMETERS"])

        if 1 != pace.PACE_STEP3D_verify_authentication_token(self.eac_ctx, pace.get_buf(token)):
            pace.print_ossl_err()
            raise SwError(SW["WARN_NOINFO63"])

        print "Established PACE channel"

        if self.at.keyref_is_can():
            if (self.sam.counter == 1):
                self.sam.active = True
                print "PIN resumed"
        elif self.at.keyref_is_pin():
            self.sam.active = True
            self.sam.counter = 3
        elif self.at.keyref_is_puk():
            self.sam.active = True
            self.sam.counter = 3
            print "PIN unblocked"

        self.eac_step += 1
        self.at.algorithm = "TA"

        self.ssc = 0

        pace.EAC_CTX_set_encryption_ctx(self.eac_ctx, pace.EAC_ID_PACE)
        pace.EAC_CTX_init_ta(self.eac_ctx, None, None, self.ca)

        return 0x9000, nPA_SE.__pack_general_authenticate([[0x86, len(my_token), my_token],
            [0x87, len(self.ca), self.ca]])

    def __eac_ca(self, data):
        tlv_data = nPA_SE.__unpack_general_authenticate(data)

        pubkey = ""
        for tag, length, value in tlv_data:
            if tag == 0x80:
                pubkey = value
            else:
                raise SwError(SW["ERR_INCORRECTPARAMETERS"])

        pace.CA_STEP4_compute_shared_secret(self.eac_ctx,
                pace.get_buf(pubkey))

        nonce, token = pace.CA_STEP5_derive_keys(self.eac_ctx,
                pace.get_buf(pubkey))

        # TODO activate SM

        return 0x9000, nPA_SE.__pack_general_authenticate([[0x81,
            len(nonce), nonce], [0x82, len(token), token]])

    def verify_certificate(self, p1, p2, data):
        if (p1, p2) != (0x00, 0xbe):
            raise SwError(SW["ERR_INCORRECTPARAMETERS"])

        cert = bertlv_pack([[0x7f21, len(data), data]])
        if 1 != pace.TA_STEP2_import_certificate(self.eac_ctx, cert):
            pace.print_ossl_err()
            raise SwError(SW["ERR_NOINFO69"]) 

        print "Imported Certificate"

        return ""

    def external_authenticate(self, p1, p2, data):
        """
        Authenticate the terminal to the card. Check whether Terminal correctly
        encrypted the given challenge or not
        """
        if self.dst.keyref: # TODO check if this is the correct CAR
            id_picc = pace.EAC_Comp(self.eac_ctx, pace.EAC_ID_PACE, self.my_pace_eph_pubkey)

            # FIXME auxiliary_data might be from an older run of PACE
            if hasattr(self.at, "auxiliary_data"):
                auxiliary_data = pace.get_buf(self.at.auxiliary_data)
            else:
                auxiliary_data = None

            if 1 != pace.TA_STEP6_verify(self.eac_ctx,
                    pace.get_buf(self.at.eph_pub_key), id_picc,
                    auxiliary_data, pace.get_buf(data)):
                pace.print_ossl_err()
                raise SwError(SW["ERR_CONDITIONNOTSATISFIED"])

            print "Terminal's signature verified"

            self.eac_step += 1

            return 0x9000, ""

        raise SwError(SW["ERR_CONDITIONNOTSATISFIED"])

    def compute_cryptographic_checksum(self, p1, p2, data):
        checksum = pace.EAC_authenticate(self.eac_ctx, self.ssc, data)
        if not checksum:
            pace.print_ossl_err()
            raise SwError(SW["ERR_NOINFO69"]) 

        return checksum

    def encipher(self, p1, p2, data):
        padded = vsCrypto.append_padding(self.cct.blocklength, data)
        cipher = pace.EAC_encrypt(self.eac_ctx, self.ssc, padded)
        if not cipher:
            pace.print_ossl_err()
            raise SwError(SW["ERR_NOINFO69"]) 

        return cipher

    def decipher(self, p1, p2, data):
        plain = pace.EAC_decrypt(self.eac_ctx, self.ssc, data)
        if not plain:
            pace.print_ossl_err()
            raise SwError(SW["ERR_NOINFO69"]) 

        return plain

    def protect_response(self, sw, result):
        """
        This method protects a response APDU using secure messaging mechanisms
        
        :returns: the protected data and the SW bytes
        """

        return_data = ""

        if result != "":
            # Encrypt the data included in the RAPDU
            encrypted = self.encipher(0x82, 0x80, result)
            encrypted = "\x01" + encrypted
            encrypted_tlv = bertlv_pack([(
                                SM_Class["CRYPTOGRAM_PADDING_INDICATOR_ODD"],
                                len(encrypted),
                                encrypted)])
            return_data += encrypted_tlv 

        sw_str = inttostring(sw)
        length = len(sw_str) 
        tag = SM_Class["PLAIN_PROCESSING_STATUS"]
        tlv_sw = bertlv_pack([(tag, length, sw_str)])
        return_data += tlv_sw
        
        if self.cct.algorithm == None:
            raise SwError(SW["CONDITIONSNOTSATISFIED"])
        elif self.cct.algorithm == "CC":
            tag = SM_Class["CHECKSUM"]
            padded = vsCrypto.append_padding(self.cct.blocklength, return_data)
            auth = self.compute_cryptographic_checksum(0x8E, 0x80, padded)
            length = len(auth)
            return_data += bertlv_pack([(tag, length, auth)])
        elif self.cct.algorithm == "SIGNATURE":
            tag = SM_Class["DIGITAL_SIGNATURE"]
            hash = self.hash(0x90, 0x80, return_data)
            auth = self.compute_digital_signature(0x9E, 0x9A, hash)
            length = len(auth)
            return_data += bertlv_pack([(tag, length, auth)])
        
        return sw, return_data


class nPA_SAM(SAM):

    def __init__(self, pin, can, mrz, puk, mf, default_se = nPA_SE):
        SAM.__init__(self, pin, None, mf)
        self.active = True
        self.current_SE = default_se(self.mf, self)
        self.can = can
        self.mrz = mrz
        self.puk = puk
        self.counter_puk = 10

    def general_authenticate(self, p1, p2, data):
        return self.current_SE.general_authenticate(p1, p2, data)

    def reset_retry_counter(self, p1, p2, data):
        # check if PACE was successful
        if self.current_SE.eac_step < 4:
            raise SwError(SW["ERR_SECSTATUS"]) 

        # TODO check CAN and PIN for the correct character set
        if p1 == 0x02:
            # change secret
            if p2 == self.current_SE.at.PACE_CAN:
                self.can = data
                print "Changed CAN to %r" % self.can
            elif p2 == self.current_SE.at.PACE_PIN:
                # TODO allow terminals to change the PIN with permission "CAN allowed"
                if not self.current_SE.at.keyref_is_pin():
                    raise SwError(SW["ERR_CONDITIONNOTSATISFIED"]) 
                self.PIN = data
                print "Changed PIN to %r" % self.PIN
            else:
                raise SwError(SW["ERR_DATANOTFOUND"])
        elif p1 == 0x03:
            # resume/unblock secret
            if p2 == self.current_SE.at.PACE_CAN:
                # CAN has no counter
                pass
            elif p2 == self.current_SE.at.PACE_PIN:
                if self.current_SE.at.keyref_is_can():
                    self.active = True
                    print "Resumed PIN"
                elif self.current_SE.at.keyref_is_pin():
                    # PACE was successful with PIN, nothing to do resume/unblock
                    pass
                elif self.current_SE.at.keyref_is_puk():
                    # TODO unblock PIN for signature
                    print "Unblocked PIN"
                    self.active = True
                    self.counter = 3
                else:
                    raise SwError(SW["ERR_CONDITIONNOTSATISFIED"]) 
            else:
                raise SwError(SW["ERR_DATANOTFOUND"])
        else:
            raise SwError(SW["ERR_INCORRECTP1P2"])

        return 0x9000, ""

    def external_authenticate(self, p1, p2, data):
        return self.current_SE.external_authenticate(p1, p2, data)

    def get_challenge(self, p1, p2, data):
        if self.current_SE.eac_step == 4:
            # TA
            if (p1 != 0x00 or p2 != 0x00):
                raise SwError(SW["ERR_INCORRECTP1P2"])
        
            self.last_challenge = pace.buf2string(pace.TA_STEP4_get_nonce(self.current_SE.eac_ctx))
            if not self.last_challenge:
                pace.print_ossl_err()
                raise SwError(SW["ERR_NOINFO69"])
        else:
            SAM.get_challenge(self, p1, p2, data)

        return SW["NORMAL"], self.last_challenge

    def parse_SM_CAPDU(self, CAPDU, header_authentication):
        self.current_SE.ssc += 1
        return SAM.parse_SM_CAPDU(self, CAPDU, header_authentication)

    def protect_result(self, sw, unprotected_result):
        self.current_SE.ssc += 1
        return SAM.protect_result(self, sw, unprotected_result)