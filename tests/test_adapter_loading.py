import copy
import tempfile
import unittest

import torch

from transformers import ADAPTER_CONFIG_MAP, AdapterType, BertModel, BertModelWithHeads, RobertaModel, XLMRobertaModel

from .test_modeling_common import ids_tensor
from .utils import require_torch


def create_twin_models(model_class):
    model_config = model_class.config_class
    model1 = model_class(model_config())
    model1.eval()
    # create a twin initialized with the same random weights
    model2 = copy.deepcopy(model1)
    model2.eval()
    return model1, model2


@require_torch
class AdapterModelTest(unittest.TestCase):

    model_classes = [BertModel, RobertaModel, XLMRobertaModel]

    def test_add_adapter(self):
        for model_class in self.model_classes:
            model_config = model_class.config_class
            model = model_class(model_config())

            for config_name, adapter_config in ADAPTER_CONFIG_MAP.items():
                for type_name, adapter_type in AdapterType.__members__.items():
                    # skip configs without invertible language adapters
                    if adapter_type == AdapterType.text_lang and not adapter_config.invertible_adapter:
                        continue
                    with self.subTest(model_class=model_class, config=config_name, adapter_type=type_name):
                        name = f"{type_name}-{config_name}"
                        model.add_adapter(name, adapter_type, config=adapter_config)

                        # adapter is correctly added to config
                        self.assertTrue(name in model.config.adapters.adapter_list(adapter_type))
                        self.assertEqual(adapter_config, model.config.adapters.get(name))

                        # check forward pass
                        input_ids = ids_tensor((1, 128), 1000)
                        input_data = {"input_ids": input_ids}
                        if adapter_type == AdapterType.text_task:
                            input_data["adapter_tasks"] = [name]
                        elif adapter_type == AdapterType.text_lang:
                            input_data["language"] = name
                        adapter_output = model(**input_data)
                        base_output = model(input_ids)
                        self.assertEqual(len(adapter_output), len(base_output))
                        self.assertFalse(torch.equal(adapter_output[0], base_output[0]))

    def test_load_adapter(self):
        for model_class in self.model_classes:
            model1, model2 = create_twin_models(model_class)

            for name, adapter_type in AdapterType.__members__.items():
                with self.subTest(model_class=model_class, adapter_type=name):
                    model1.add_adapter(name, adapter_type)
                    with tempfile.TemporaryDirectory() as temp_dir:
                        model1.save_adapter(temp_dir, name)

                        model2.load_adapter(temp_dir)

                    # check if adapter was correctly loaded
                    self.assertTrue(name in model2.config.adapters.adapter_list(adapter_type))

                    # check equal output
                    in_data = ids_tensor((1, 128), 1000)
                    output1 = model1(in_data, adapter_tasks=[name])
                    output2 = model2(in_data, adapter_tasks=[name])
                    self.assertEqual(len(output1), len(output2))
                    self.assertTrue(torch.equal(output1[0], output2[0]))


@require_torch
class PredictionHeadModelTest(unittest.TestCase):

    model_classes = [BertModelWithHeads]

    def run_prediction_head_test(self, model, compare_model, head_name, input_shape=(1, 128), output_shape=(1, 2)):
        with tempfile.TemporaryDirectory() as temp_dir:
            model.save_head(temp_dir, head_name)

            compare_model.load_head(temp_dir)

        # check if adapter was correctly loaded
        self.assertTrue(head_name in compare_model.config.prediction_heads)

        in_data = ids_tensor(input_shape, 1000)
        model.set_active_task(head_name)
        output1 = model(in_data)
        self.assertEqual(output_shape, tuple(output1[0].size()))
        # check equal output
        compare_model.set_active_task(head_name)
        output2 = compare_model(in_data)
        self.assertEqual(len(output1), len(output2))
        self.assertTrue(torch.equal(output1[0], output2[0]))

    def test_classification_head(self):
        for model_class in self.model_classes:
            model1, model2 = create_twin_models(model_class)

            with self.subTest(model_class=model_class):
                model1.add_classification_head("dummy")
                self.run_prediction_head_test(model1, model2, "dummy")

    def test_multiple_choice_head(self):
        for model_class in self.model_classes:
            model1, model2 = create_twin_models(model_class)

            with self.subTest(model_class=model_class):
                model1.add_multiple_choice_head("dummy")
                self.run_prediction_head_test(model1, model2, "dummy", input_shape=(2, 128))

    def test_tagging_head(self):
        for model_class in self.model_classes:
            model1, model2 = create_twin_models(model_class)

            with self.subTest(model_class=model_class):
                model1.add_tagging_head("dummy")
                self.run_prediction_head_test(model1, model2, "dummy", output_shape=(1, 128, 2))

    def test_adapter_with_head(self):
        for model_class in self.model_classes:
            model1, model2 = create_twin_models(model_class)

            with self.subTest(model_class=model_class):
                name = "dummy"
                model1.add_adapter(name, AdapterType.text_task)
                model1.add_classification_head(name, num_labels=3)
                model1.set_active_task(name)
                with tempfile.TemporaryDirectory() as temp_dir:
                    model1.save_adapter(temp_dir, name)

                    model2.load_adapter(temp_dir)
                    model2.set_active_task(name)

                # check equal output
                in_data = ids_tensor((1, 128), 1000)
                output1 = model1(in_data)
                output2 = model2(in_data)
                self.assertEqual(len(output1), len(output2))
                self.assertTrue(torch.equal(output1[0], output2[0]))
                self.assertEqual(3, output1[0].size()[1])
