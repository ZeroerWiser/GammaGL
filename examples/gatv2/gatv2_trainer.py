# !/usr/bin/env python3
# -*- coding:utf-8 -*-

# @Time    : 2022/03/14 10:23
# @Author  : hanhui
# @FileName: gatv2_trainer.py


import os
os.environ['TL_BACKEND'] = 'paddle'
# os.environ["CUDA_VISIBLE_DEVICES"] = "9"
import sys
# sys.path.insert(0, os.path.abspath('../'))  # adds path2gammagl to execute in command line.
import argparse
import tensorlayerx as tlx
from gammagl.datasets import Planetoid
from gammagl.models import GATV2Model
from gammagl.utils.loop import add_self_loops
from tensorlayerx.model import TrainOneStep, WithLoss


class SemiSpvzLoss(WithLoss):
    def __init__(self, net, loss_fn):
        super(SemiSpvzLoss, self).__init__(backbone=net, loss_fn=loss_fn)

    def forward(self, data, label):
        logits = self._backbone(data['x'], data['edge_index'], data['num_nodes'])
        if tlx.BACKEND == 'mindspore':
            idx = tlx.convert_to_tensor([i for i, v in enumerate(data['train_mask']) if v], dtype=tlx.int64)
            train_logits = tlx.gather(logits, idx)
            train_label = tlx.gather(label, idx)
        else:
            train_logits = logits[data['train_mask']]
            train_label = label[data['train_mask']]
        loss = self._loss_fn(train_logits, train_label)
        return loss


def evaluate(net, data, y, mask, metrics):
    net.set_eval()
    logits = net(data['x'], data['edge_index'], data['num_nodes'])
    if tlx.BACKEND == 'mindspore':
        idx = tlx.convert_to_tensor([i for i, v in enumerate(mask) if v], dtype=tlx.int64)
        _logits = tlx.gather(logits, idx)
        _label = tlx.gather(y, idx)
    else:
        _logits = logits[mask]
        _label = y[mask]
    metrics.update(_logits, _label)
    acc = metrics.result()
    metrics.reset()
    return acc


def main(args):
    # load cora dataset
    if str.lower(args.dataset) not in ['cora', 'pubmed', 'citeseer']:
        raise ValueError('Unknown dataset: {}'.format(args.dataset))
    dataset = Planetoid(args.dataset_path, args.dataset)
    dataset.process()  # suggest to execute explicitly so far
    graph = dataset[0]
    graph.tensor()
    edge_index, _ = add_self_loops(graph.edge_index, n_loops=args.self_loops)
    x = graph.x
    y = tlx.argmax(graph.y, axis=1)

    net = GATV2Model(feature_dim=x.shape[1],
                     hidden_dim=args.hidden_dim,
                     heads=args.heads,
                     num_class=graph.y.shape[1],
                     drop_rate=args.drop_rate,
                     name="GATV2")
    optimizer = tlx.optimizers.Adam(lr=args.lr, weight_decay=args.l2_coef)
    metrics = tlx.metrics.Accuracy()
    train_weights = net.trainable_weights

    loss_func = SemiSpvzLoss(net, tlx.losses.softmax_cross_entropy_with_logits)
    train_one_step = TrainOneStep(loss_func, optimizer, train_weights)

    data = {
        "x": x,
        "edge_index": edge_index,
        "train_mask": graph.train_mask,
        "test_mask": graph.test_mask,
        "val_mask": graph.val_mask,
        "num_nodes": graph.num_nodes,
    }

    best_val_acc = 0.
    for epoch in range(args.n_epoch):
        net.set_train()
        train_loss = train_one_step(data, y)
        val_acc = evaluate(net, data, y, data['val_mask'], metrics)

        print("Epoch [{:0>3d}]  ".format(epoch + 1)
              + "   train loss: {:.4f}".format(train_loss.item())
              + "   val acc: {:.4f}".format(val_acc))

        # save best model on evaluation set
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            net.save_weights(args.best_model_path+net.name+".npz", format='npz_dict')

    net.load_weights(args.best_model_path+net.name+".npz", format='npz_dict')
    test_acc = evaluate(net, data, y, data['test_mask'], metrics)
    print("Test acc:  {:.4f}".format(test_acc))


if __name__ == '__main__':
    # parameters setting
    parser = argparse.ArgumentParser()
    parser.add_argument("--lr", type=float, default=0.005, help="learnin rate")
    parser.add_argument("--n_epoch", type=int, default=200, help="number of epoch")
    parser.add_argument("--hidden_dim", type=int, default=8, help="dimension of hidden layers")
    parser.add_argument("--heads", type=int, default=8, help="number of heads for stablization")
    parser.add_argument("--drop_rate", type=float, default=0.4, help="drop_rate")
    parser.add_argument("--l2_coef", type=float, default=1e-4, help="l2 loss coeficient")
    parser.add_argument('--dataset', type=str, default='cora', help='dataset')
    parser.add_argument("--dataset_path", type=str, default=r'../', help="path to save dataset")
    parser.add_argument("--best_model_path", type=str, default=r'./', help="path to save best model")
    parser.add_argument("--self_loops", type=int, default=1, help="number of graph self-loop")
    args = parser.parse_args()

    main(args)