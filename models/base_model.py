import torch
from torch import nn
import os
from .networks import get_scheduler


class BaseModel:
    @staticmethod
    def modify_commandline_options(parser, is_train):
        return parser

    def name(self):
        return self.__class__.__name__

    def initialize(self, opt):
        self.opt = opt
        self.gpu_ids = opt.gpu_ids
        self.is_train = opt.is_train
        self.device = torch.device("cpu")
        # self.device = torch.device(
        #     "cuda:{}".format(self.gpu_ids[0]) if self.gpu_ids else torch.device("cpu")
        # )
        self.save_dir = os.path.join(opt.checkpoints_dir, opt.name)
        torch.backends.cudnn.benchmark = True

        self.loss_names = []  # losses to report
        self.model_names = []  # models that will be used
        self.visual_names = []  # visuals to show at test time

    def set_input(self, input: dict):
        self.input = input

    def forward(self):
        """Run the forward pass. Read from self.input, set self.output"""
        raise NotImplementedError()

    def setup(self, opt):
        """Creates schedulers if train, Load and print networks if resume"""
        if self.is_train:
            self.schedulers = [get_scheduler(optim, opt) for optim in self.optimizers]
        if opt.load_subnetworks_dir:
            nets = opt.load_subnetworks.split(",")
            self.load_subnetworks(
                opt.load_subnetworks_epoch,
                opt.load_subnetworks.split(","),
                opt.load_subnetworks_dir,
            )
            print("loading pretrained {}".format(nets))
        if not self.is_train or opt.resume_dir:
            self.load_networks(opt.resume_epoch)
        if opt.freeze_subnetworks:
            nets = opt.freeze_subnetworks.split(",")
            self.freeze_subnetworks(opt.freeze_subnetworks.split(","))
            print("freezing {}".format(nets))

        self.print_networks(opt.verbose)

    def eval(self):
        """turn on eval mode"""
        for net in self.get_networks():
            net.eval()

    def train(self):
        for net in self.get_networks():
            net.train()

    def test(self):
        with torch.no_grad():
            self.forward()

    def get_networks(self) -> [nn.Module]:
        ret = []
        for name in self.model_names:
            assert isinstance(name, str)
            net = getattr(self, "net_{}".format(name))
            assert isinstance(net, nn.Module)
            ret.append(net)
        return ret

    def get_current_visuals(self):
        ret = {}
        for name in self.visual_names:
            assert isinstance(name, str)
            ret[name] = getattr(self, name)
        return ret

    def get_current_losses(self):
        ret = {}
        for name in self.loss_names:
            assert isinstance(name, str)
            ret[name] = getattr(self, "loss_" + name)
        return ret

    def get_subnetworks(self) -> dict:
        raise NotImplementedError()

    def freeze_subnetworks(self, network_names):
        nets = self.get_subnetworks()
        for name in network_names:
            nets[name].requires_grad_(False)

    def unfreeze_subnetworks(self, network_names):
        nets = self.get_subnetworks()
        for name in network_names:
            nets[name].requires_grad_(True)

    def save_subnetworks(self, epoch):
        nets = self.get_subnetworks()
        for name, net in nets.items():
            save_filename = "{}_subnet_{}.pth".format(epoch, name)
            save_path = os.path.join(self.save_dir, save_filename)
            try:
                if isinstance(net, nn.DataParallel):
                    net = net.module
                torch.save(net.state_dict(), save_path)
            except Exception as e:
                print(e)

    def load_subnetworks(self, epoch, names=None, resume_dir=None):
        networks = self.get_subnetworks()
        if names is None:
            names = set(networks.keys())
        else:
            names = set(names)

        for name, net in networks.items():
            if name not in names:
                continue

            load_filename = "{}_subnet_{}.pth".format(epoch, name)
            load_path = os.path.join(
                resume_dir if resume_dir is not None else self.opt.resume_dir,
                load_filename,
            )

            if not os.path.isfile(load_path):
                print("cannot load", load_path)
                continue

            state_dict = torch.load(load_path, map_location=self.device)
            if isinstance(net, nn.DataParallel):
                net = net.module

            net.load_state_dict(state_dict, strict=True)

    def save_networks(self, epoch, other_states={}):
        for name, net in zip(self.model_names, self.get_networks()):
            save_filename = "{}_net_{}.pth".format(epoch, name)
            save_path = os.path.join(self.save_dir, save_filename)

            try:
                if isinstance(net, nn.DataParallel):
                    net = net.module
                torch.save(net.state_dict(), save_path)
            except Exception as e:
                print(e)

        save_filename = "{}_states.pth".format(epoch)
        save_path = os.path.join(self.save_dir, save_filename)
        torch.save(other_states, save_path)

    def load_networks(self, epoch):
        for name, net in zip(self.model_names, self.get_networks()):
            print("loading", name)
            assert isinstance(name, str)
            load_filename = "{}_net_{}.pth".format(epoch, name)
            load_path = os.path.join(self.opt.resume_dir, load_filename)

            if not os.path.isfile(load_path):
                print("cannot load", load_path)
                continue

            state_dict = torch.load(load_path, map_location=self.device)
            if isinstance(net, nn.DataParallel):
                net = net.module

            net.load_state_dict(state_dict, strict=False)

    def print_networks(self, verbose):
        print("------------------- Networks -------------------")
        for name, net in zip(self.model_names, self.get_networks()):
            num_params = 0
            for param in net.parameters():
                num_params += param.numel()
            if verbose:
                print(net)
            print(
                "[Network {}] Total number of parameters: {:.3f}M".format(
                    name, num_params / 1e6
                )
            )
        print("------------------------------------------------")

    def set_requires_grad(self, nets, requires_grad):
        if not isinstance(nets, list):
            nets = [nets]
        for net in nets:
            if net:
                for param in net.parameters():
                    param.requires_grad = requires_grad

    def update_learning_rate(self, verbose=False):
        for scheduler in self.schedulers:
            scheduler.step()
        for i, optim in enumerate(self.optimizers):
            lr = optim.param_groups[0]["lr"]
            if verbose:
                print("optimizer {}, learning rate = {:.7f}".format(i + 1, lr))

    def set_current_step(self, step=None):
        self.current_step = step
