import unittest

from file_chisel.proposal import parse_proposal_json


class ProposalTests(unittest.TestCase):
    def test_decodes_a_json_object(self):
        result = parse_proposal_json('{"schema_version": 1}')

        self.assertEqual(result, {"schema_version": 1})

    def test_rejects_a_json_list(self):
        with self.assertRaises(ValueError):
            parse_proposal_json("[]")

    def test_rejects_invalid_json(self):
        with self.assertRaises(ValueError):
            parse_proposal_json('{"schema_version":')


if __name__ == "__main__":
    unittest.main()
